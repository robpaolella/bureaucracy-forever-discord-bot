import asyncio

from sync.runner import JobRunner


class FakeApi:
    def __init__(self):
        self.acks = []

    async def ack(self, job_id, *, ok, result=None, error=None):
        self.acks.append((job_id, ok))
        return {}


def test_a_job_handed_out_twice_while_in_flight_runs_once():
    api = FakeApi()
    runner = JobRunner(api, poll_seconds=0.01)  # type: ignore[arg-type]
    runs = []

    async def slow(job):
        runs.append(job["id"])
        await asyncio.sleep(0.03)
        return {}

    runner.handle("application.update", slow)

    async def go():
        job = {"id": "j1", "type": "application.update", "entity": "application:a"}
        runner.dispatch(job)
        await asyncio.sleep(0.005)
        runner.dispatch(dict(job))  # the site re-handed it after a lock expiry
        await asyncio.sleep(0.1)

    asyncio.run(go())
    assert runs == ["j1"]
    assert api.acks == [("j1", True)]
