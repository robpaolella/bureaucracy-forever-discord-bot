import asyncio

from sync.runner import JobRunner


class FakeApi:
    def __init__(self):
        self.acks = []

    async def ack(self, job_id, *, ok, result=None, error=None):
        self.acks.append((job_id, ok, result, error))
        return {"status": "DONE" if ok else "PENDING"}


def run(coro):
    return asyncio.run(coro)


def test_jobs_run_in_order_per_entity_and_in_parallel_across_entities():
    api = FakeApi()
    runner = JobRunner(api, poll_seconds=0.01, concurrency=4)  # type: ignore[arg-type]
    order = []

    async def handler(job):
        await asyncio.sleep(0.02 if job["id"] == "a1" else 0.001)
        order.append(job["id"])
        return {"seen": job["id"]}

    runner.handle("application.update", handler)

    async def go():
        for j in ({"id": "a1", "type": "application.update", "entity": "application:a"}, {"id": "a2", "type": "application.update", "entity": "application:a"}, {"id": "b1", "type": "application.update", "entity": "application:b"}):
            runner.dispatch(j)
        await asyncio.sleep(0.1)

    run(go())
    # a1 finishes after b1 (it sleeps longer) but always before a2: same entity, in order.
    assert order.index("a1") < order.index("a2")
    assert order.index("b1") < order.index("a1")
    assert [a[1] for a in api.acks] == [True, True, True]


def test_unknown_types_fail_and_deferred_types_wait():
    api = FakeApi()
    runner = JobRunner(api, poll_seconds=0.01)  # type: ignore[arg-type]
    runner.defer("raid.post")

    async def go():
        runner.dispatch({"id": "x1", "type": "something.new", "entity": "misc"})
        runner.dispatch({"id": "r1", "type": "raid.post", "entity": "raid:1"})
        await asyncio.sleep(0.05)

    run(go())
    assert api.acks == [("x1", False, None, "unknown type")]


def test_handler_exceptions_are_reported_not_swallowed():
    api = FakeApi()
    runner = JobRunner(api, poll_seconds=0.01)  # type: ignore[arg-type]

    async def boom(job):
        raise RuntimeError("thread gone")

    runner.handle("application.update", boom)

    async def go():
        runner.dispatch({"id": "e1", "type": "application.update", "entity": "application:e"})
        await asyncio.sleep(0.05)

    run(go())
    assert api.acks[0][:2] == ("e1", False)
    assert "RuntimeError: thread gone" in api.acks[0][3]
