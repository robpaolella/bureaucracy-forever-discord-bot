# Discord ↔ web sync — specification

This file is the contract between the site (`bureaucracy-forever`, Next.js on Vercel, Neon
Postgres) and the bot (Bureaucrat, discord.py, Docker on the Debian server). Put an
identical copy in both repositories. When the two sides disagree, this file wins, and the
fix is a PR to this file first.

Decisions here were made by Robert and are not open for re-litigation by the implementer:
web-only intake, rank-based rosters, outbox polling, every officer thread message becomes a
note, auto-assign Guild Member on accept, no native Discord Events, any member may bench.

---

## 1. Principles

1. **The Neon database is the only source of truth.** Discord displays it and accepts input
   for it. The bot never renders from its own state; it re-renders from the site's API.
2. **All business rules live in the site.** Who may accept, what accept does, when a raid
   locks, who is on a roster. The bot calls endpoints; it does not decide.
3. **Nothing inbound to the Debian server.** The bot polls the site over HTTPS. There is no
   webhook from the site to the bot. Remove `BOT_WEBHOOK_URL` and anything built on it.
4. **The bot is the clock.** Vercel's free cron is once per day, so the bot calls
   `POST /api/bot/tick` every 60 seconds and the site does whatever is due.
5. **Every synced object stores its Discord IDs** (thread, starter message, one per mirrored
   note). Changes are edits to existing messages, never re-posts.
6. **Every bot write is idempotent.** `Idempotency-Key` header on every POST/PATCH/DELETE,
   set to the Discord interaction ID or message ID that caused it. The site stores keys and
   replays the original response on a repeat.
7. **Fail loudly, retry quietly.** A job that fails is retried with backoff up to 5 times,
   then marked FAILED and posted to `#officers` by the bot. Nothing is silently dropped.

Guild time zone is one constant, `GUILD_TZ = America/Los_Angeles`, on both sides.

---

## 2. Authentication

- Site → nothing. The site never calls the bot.
- Bot → site: `Authorization: Bearer <BOT_SHARED_SECRET>` on every request to `/api/bot/*`.
  Any other route rejects that header. Compare with a constant-time function.
- `BOT_SHARED_SECRET` is generated once (`openssl rand -base64 33`), set on Vercel and in the
  bot's env file on the server. Never printed, never committed.

---

## 3. Schema changes (site, Prisma)

Additive migrations only. Nothing existing is renamed.

```prisma
enum Standing   { ROSTER BENCH }
enum RaidStatus { SCHEDULED LOCKED DONE CANCELLED }
enum JobStatus  { PENDING RUNNING DONE FAILED }

model Application {
  // existing fields unchanged, plus:
  source            Source    @default(WEB)   // intake is web-only; field kept for symmetry
  discordThreadId   String?   @unique          // forum post (thread) id
  discordMessageId  String?                    // starter message id
  reopenedAt        DateTime?
  nudgedAt          DateTime?                  // 24h pending nudge sent
}

model OfficerNote {
  // existing fields unchanged, plus:
  source            Source    @default(WEB)
  discordMessageId  String?   @unique          // the thread message this note IS (Discord-origin)
                                               // or the mirror the bot posted (web-origin)
  editedAt          DateTime?
  deletedAt         DateTime?                  // soft delete; hidden on web, deleted in Discord
}

model RaidTemplate {
  id            String  @id @default(cuid())
  name          String  @unique               // "Molten Core"
  short         String                        // "MC" — for calendar chips and post titles
  size          Int                           // 40, 20
  durationMin   Int     @default(180)
  requirements  Json                          // { tank, healer, melee, ranged } — sums to size
  active        Boolean @default(true)
  series        RaidSeries[]
  raids         Raid[]
}

model RaidSeries {
  id                String   @id @default(cuid())
  templateId        String
  template          RaidTemplate @relation(fields: [templateId], references: [id])
  weekday           Int                        // 0 = Sunday … 6 = Saturday, in GUILD_TZ
  startTime         String                     // "20:00", wall clock in GUILD_TZ. NOT UTC.
  durationMin       Int
  notes             String?
  postAheadDays     Int      @default(14)
  lockMinutesBefore Int      @default(120)
  horizonWeeks      Int      @default(4)
  active            Boolean  @default(true)
  createdById       String
  createdAt         DateTime @default(now())
  updatedAt         DateTime @updatedAt
  raids             Raid[]
}

model Raid {
  // existing fields unchanged (startsAt stays an absolute instant), plus:
  templateId        String?
  template          RaidTemplate? @relation(fields: [templateId], references: [id])
  seriesId          String?
  series            RaidSeries?   @relation(fields: [seriesId], references: [id], onDelete: SetNull)
  detached          Boolean   @default(false)  // edited individually; series edits skip it
  status            RaidStatus @default(SCHEDULED)
  locksAt           DateTime
  lockedAt          DateTime?
  discordThreadId   String?   @unique
  discordMessageId  String?
  postedAt          DateTime?
  remind72At        DateTime?                  // set when sent
  remind24At        DateTime?
  updatedAt         DateTime  @updatedAt       // optimistic concurrency for officer edits
  // discordEventId stays, nullable, unused. Do not build on it.
}

model Signup {
  // existing fields, with these changes:
  response   Response?                          // NOW NULLABLE: on the roster, not answered yet
  standing   Standing  @default(ROSTER)
  attended   Boolean?                           // set by officers after the raid
  // @@unique([raidId, userId]) stays
}

/// Work for the bot. The site writes; the bot polls, runs, acks.
model OutboxJob {
  id          String    @id @default(cuid())
  type        String                            // see §5
  payload     Json
  status      JobStatus @default(PENDING)
  attempts    Int       @default(0)
  runAfter    DateTime  @default(now())         // backoff
  lockedAt    DateTime?
  lastError   String?
  result      Json?                             // what the bot reported on ack
  createdAt   DateTime  @default(now())
  @@index([status, runAfter])
}

/// Replay protection for bot writes.
model BotRequest {
  key        String   @id
  statusCode Int
  body       Json
  createdAt  DateTime @default(now())
}
```

Seed `RaidTemplate` with the raid templates configured in production:
Barrow Deeps 10 (2/2/3/3), Hyjal Summit 20 (2/3/7/8), Onyxia's Lair 40 (3/12/11/14).
Order is tank/healer/melee/ranged.

Roster derivation (amended 2026-09-27, Robert's decision): Discord is the source for who is
in the guild. `User.inGuild` is true for anyone holding Guild Member or Officer; only they
appear on the web roster, with or without a main character. `User.rank` is the roster rank
and goes **both ways**: the Discord roles Officer > Trial > Raider set it (anyone else is
`SOCIAL`), and a rank set on the web roster editor pushes the matching roles back through
`member.roles.sync` (Raider for raiders and officers, Raider + Trial for trials, Social for
socials; Officer is never touched). Whichever side wrote last wins; a snapshot arriving while
a web-set rank still has an open `member.roles.sync` job leaves that rank alone. The main
character's `rank` column mirrors `User.rank`. A user is on the roster of every generated raid
if `inGuild` and `rank` is `RAIDER`, `TRIAL` or `OFFICER`; a main is not required.

The bot sends `POST /members/sync` with every guild member's id, display name, avatar and
role ids every `SNAPSHOT_SECONDS` (`full: true`, only from a complete member cache) and for one
member, in order, on every roster-relevant role change, join or leave. Members missing from a full
snapshot have left and get `inGuild = false`. Role changes the bot makes itself go out as one
`member.edit`, so Discord reports one state, not two halves.

Trials: accepting a raider application sets `rank = TRIAL`, `trialStartedAt = now`, and the
decide job adds Guild Member, Raider and Trial and removes Guest and Social. Anyone who gets
the Trial role another way (the #new-users triage, an officer by hand) starts the same clock
through the snapshot. The check-in is due at `User.trialCheckInAt`, or fourteen days after
`trialStartedAt` while that is unset; tick then enqueues one `trial.checkin` and sets
`trialNudgedAt`. The bot posts it to `#officers`, mentioning the Officer role, with **Promote to
Raider** and an **Extend trial** menu (1 to 7 days), both answered through
`POST /members/:discordId/trial`. Promote sets rank RAIDER the way the roster editor does (the
Trial role comes off through `member.roles.sync`); extend sets `trialCheckInAt = now + days` and
clears `trialNudgedAt`, so the check-in comes back then. Leaving Trial by any route clears
`trialStartedAt`, `trialNudgedAt` and `trialCheckInAt`. This is the only trial reminder;
officers may still end a trial on the roster editor. The trial DM text is a placeholder until
Robert writes the real one.

---

## 4. Site API for the bot

All under `/api/bot/`. JSON in, JSON out (204 has no body). 401 on bad secret. 409 on a state
conflict with a `reason` string the bot shows verbatim to the user who clicked, except the
character routes below: their `reason` is a stable code and `error` is the message to show.

| Method | Route | Purpose |
|---|---|---|
| GET | `/outbox?limit=20` | Next pending jobs whose `runAfter` has passed. Marks them RUNNING with `lockedAt`. Jobs locked > 5 min are returned again. |
| POST | `/outbox/:id/ack` | `{ ok: true, result? }` → DONE, store result, apply it (see §5). `{ ok: false, error }` → attempts+1, PENDING with `runAfter = now + 2^attempts min`, FAILED after 5. |
| POST | `/tick` | Runs everything due (§6). Idempotent. Returns counts of what it did. |
| GET | `/applications/:id` | Full application + notes, for rendering. |
| POST | `/applications/:id/decision` | `{ status: ACCEPTED\|DECLINED, byDiscordId, reason? }`. 409 if not PENDING. |
| POST | `/applications/:id/reopen` | `{ byDiscordId }`. 409 if PENDING. |
| POST | `/applications/:id/notes` | `{ discordMessageId, authorDiscordId, body, createdAt }`. Author must resolve to an OFFICER; otherwise 403 and the bot ignores the message. |
| PATCH | `/notes/by-message/:discordMessageId` | `{ body }` |
| DELETE | `/notes/by-message/:discordMessageId` | soft delete |
| GET | `/raids/:id` | Raid + template + counts + `viewer` block when `?discordId=` is given: `{ standing, response, character, characters?, lootTable, reservesLocked, reservesComplete, reservesUrl }`, or `null` when that member has no sign-up (so cannot reserve). `lootTable` is true when loot is on and the raid's template has at least one loot-table item; `reservesLocked` is true from `startsAt − 120 min` (the reserve lock, not the sign-up lock); `reservesComplete` is true only when the member has both a hard and a soft reserve on the raid, as for `raid.reserves.remind`; `reservesUrl` is `<site>/members/calendar/<raidId>?reserves=1`, which opens that member's own reserves window after sign-in when they are Accept or Tentative with a character, the raid is not cancelled and reserves aren't locked, and otherwise just shows the raid page. `character` is the brought character `{ id, name, wowClass, spec, raidRole }` (saved choice, otherwise main), or `null`; `wowClass` and `raidRole` are lowercase. `characters` is the member's list in the `/members/:discordId` format, main first then alts by name, sent only when the member has alts. `POST /raids/:id/respond` answers with the same `viewer` block. The bot must tolerate a missing `character` on older sites and show no character menu without `characters`. Character names are private to this viewer block, never added to the embed (§8). |
| POST | `/raids/:id/respond` | `{ discordId, response: ACCEPT\|TENTATIVE\|ABSENT, reason?, characterId? }`. Optional `characterId` must belong to the member; invalid/not theirs, or any character with ABSENT, returns 400. Omitted: preserve the saved choice, or use the main for a new sign-up. A new sign-up can choose an alt directly. For an existing choice that differs, write the answer then switch character in the same transaction using the shared reserve rule; a switch refusal rolls back the entire answer and returns its status and `{ reason }`. Success includes `removedReserves: [{ kind, itemName, reason }]` (no item ids; `[]` when none or no switch), so the bot can explain reserves the new character cannot take. Uses `Idempotency-Key`, replaying the complete reply. Rules in §7, including the sign-up lock, are unchanged. `/bench` does not accept character choice. |
| POST | `/raids/:id/bench` | `{ discordId }` — opt onto the bench. 409 if already on roster. |
| POST | `/raids/:id/attendance` | `{ byDiscordId, attended: [discordId], absent: [discordId] }`. Officers only, after `startsAt + durationMin`. |
| GET | `/members/:discordId` | role, rank, guild membership, main character (existing fields unchanged), plus `characters: [{ id, name, wowClass, spec, raidRole, isMain }]`, main first then alts by name. `wowClass` and `raidRole` are lowercase. 404 if unknown. |
| POST | `/members/:discordId/characters` | `{ name, wowClass, spec, raidRole }` adds an alt using the site's character rules; `raidRole` maps to the rules' `role`. 201 `{ character: { id, name, wowClass, spec, raidRole, isMain } }`; same-name alt for this member returns 200 with the existing character, without changing it. 404 unknown member; 400 `{ reason: "invalid", error }`; 413 `{ error }` for an oversized body; 409 `{ reason: "name_taken"\|"limit"\|"no_main"\|"busy", error }` (`busy` means retries lost a race; retry with a new interaction/key, since the same key replays the same failure). Maximum eight characters including main. Uses `Idempotency-Key`; a replay preserves the original status and body. Does not change Discord class tags. |
| DELETE | `/members/:discordId/characters/:characterId` | Removes an alt, never a main (including a lone main). 204 with no body when removed or already gone, including a concurrent removal. 404 unknown member or another member's character; 409 `{ reason: "main"\|"busy", error }` if main or retries exhausted (`busy` requires a new interaction/key to retry; the same key replays the failure). Uses `Idempotency-Key`, including bodyless 204 replay. Does not change Discord class tags. |
| PUT | `/members/:discordId/main` | `{ firstName, secondName, wowClass, spec, raidRole }` from the "Set my main" flow (§9.7). Creates or replaces the member's main on the roster; rank untouched. 404 unknown member, 409 name taken. |
| GET | `/classes` | Classes, specs and the raid roles each spec fills, for the bot's menus. |
| POST | `/members/:discordId/trial` | `{ action: promote\|extend, days?, byDiscordId }` from the trial check-in (§3). `byDiscordId` must be an OFFICER, else 403. `days` is 1–7 for extend. 404 unknown member; 409 with `reason` when they are not a trial any more. Answers `{ action, rank, checkInAt? }`. |
| GET | `/needs` | Recruitment needs for `/recruitment` (§9.8): `{ statuses: [high, medium, closed], classes: [{ key, label, specs: [{ name, status }] }] }`, every spec listed, closed unless an officer set it. A spec that fills two raid roles is listed once per role, each with its own status: Feral is `Feral Tank` and `Feral Melee DPS`. |
| PUT | `/needs` | `{ wowClass, spec, status, byDiscordId }` from `/recruitment`; `spec` is a name from `GET /needs`. `byDiscordId` must be an OFFICER, else 403; 400 for an unknown class, spec or status. The same write as the web needs editor, so the recruitment page, the home teaser and the officer editor show it within a minute. Anything but high also takes the spec's home-page star away. Answers `{ wowClass, spec, status }`. |
| POST | `/members/sync` | `{ members: [{ discordId, name, avatarUrl, roles }], full }`. Upserts every member's name, avatar, site role, `inGuild` and rank from their Discord roles (§3). With `full`, anyone not listed gets `inGuild = false`. Idempotent; no Idempotency-Key needed. |
| GET | `/health` | `{ ok: true, version }` |

Every write here that changes something Discord shows **also enqueues the matching
`*.update` job**. That is how a click in Discord ends up re-rendering the embed: the bot
posts the response, the site stores it and enqueues `raid.update`, the bot picks that up on
its next poll and re-renders from `GET /raids/:id`. The bot must not update the embed
directly from the click; it may reply ephemerally ("You're set to Accept") immediately.

---

## 5. Outbox job types

`payload` always includes the entity id. The bot handles each type; unknown types are acked
`ok: false` with `error: "unknown type"` so they surface as FAILED.

| Type | Bot does | Ack `result` | Site applies result |
|---|---|---|---|
| `application.post` | Create forum post in `APPLICATIONS_FORUM_ID`: title `Name — Class (Spec) — Path` (Social: `Name — Social`), tags `[path, Pending]`, starter = embed of all answers + buttons Accept / Decline / Open on web (link). | `{ threadId, messageId }` | store on Application |
| `application.update` | Re-render starter embed and tags from `GET /applications/:id`. | — | — |
| `application.note.post` | Post `**Name** (web) · body` in the thread. | `{ messageId }` | store on OfficerNote |
| `application.note.edit` | Edit that message. | — | — |
| `application.note.delete` | Delete that message. | — | — |
| `application.decide` | Post decision embed (green Accepted / red Declined, by whom, via web or Discord). Retag. DM applicant from the template in `content/dm-templates` (site serves it in the job payload). On ACCEPTED: apply `payload.roles` (`{ add, remove }`, computed by the site: Guild Member, Raider and Trial in, Guest and Social out for the raider path; Guild Member and Social in, Guest out for social). Archive + lock the thread. | `{ dmDelivered: bool }` | — |
| `application.reopen` | Unarchive, unlock, retag Pending, post "Reopened by X". | — | — |
| `application.nudge` | Message in `OFFICERS_CHANNEL_ID`: "Application from X has been pending 24h" + link. | — | — |
| `raid.post` | Post one message in `RAID_SIGNUPS_CHANNEL_ID`: embed (§8) titled `🟢 Template — Ddd Mon D` (+ " · added late" when `payload.late`) + buttons Accept / Tentative / Decline / Join bench / View roster (link); open a thread on it named like the title minus the state marker. | `{ threadId, messageId }` | store on Raid |
| `raid.update` | Re-render the embed (title prefix and colour carry the state) from `GET /raids/:id`. | — | — |
| `raid.remind` | Thread message mentioning `payload.discordIds`. For anyone the mention can't reach (left server), skip. | — | — |
| `raid.reserves.remind` | Tell `payload.discordIds` they haven't picked both loot reserves; link to the raid page's Loot reserves section. Skip members the bot cannot reach. Payload: `{ raidId, discordIds: string[], reservesLockAt: ISO timestamp }`. Ordered with other jobs for this raid. | — | — |
| `raid.lock` | Re-render as 🔒 Locked, post "Sign-ups are locked. Officers can still change answers on the web." in the thread. | — | — |
| `raid.cancel` | Edit the message to the compact ❌ line with `payload.reason`, remove buttons, DM everyone who ACCEPTed, archive the thread. | — | — |
| `raid.close` | Edit the message to the compact ✅ line with the attended count, remove buttons, archive the thread. | — | — |
| `raid.delete` | `{ raidId, threadId, messageId }`: the raid row is already gone, so nothing is fetched. Delete the thread (archive it when the bot lacks Manage Threads), then the message. Quiet: no thread notice, no DMs. Missing objects count as done. | — | — |
| `member.roles.sync` | `{ discordId, add: [roleId], remove: [roleId] }`. Only ever touches Guild Member, Guest, Raider, Trial and Social. **Never Officer** — that role grants site access and is managed by humans. | — | — |
| `officers.notify` | Free-text message to `#officers`. Used for FAILED jobs and reconcile findings. | — | — |
| `trial.checkin` | `{ discordId, name, startedAt, extended }` (§3). Message in `#officers` mentioning the Officer role: whose trial is up and since when, with **Promote to Raider** (button) and **Extend trial** (menu, 1–7 days). Both call `POST /members/:discordId/trial` and survive restarts; the answer edits the message to the outcome and removes them. Entity `member:<discordId>`. | — | — |

The `raid.post` / `raid.update` / `raid.lock` / `raid.cancel` / `raid.close` rows describe
the forum-era mechanism; since #raid-signups became a text channel (§8) the bot posts one
message plus a thread instead of a forum post, and state is the embed's title prefix and
colour instead of tags. The site-facing contract is identical: payloads, the ack result
`{ threadId, messageId }`, and what the site stores (`Raid.discordMessageId` is the channel
message, `Raid.discordThreadId` the thread).

The bot processes jobs **in order per entity** (same `applicationId`/`raidId` never in
parallel) and up to 4 entities concurrently.

---

## 6. What `tick` does

Runs every 60 s, called by the bot. Each step is idempotent and bounded.

1. **Generate instances.** For every active series, ensure a Raid exists for each occurrence
   from now to `horizonWeeks` ahead. Compute `startsAt` from weekday + `startTime` in
   `GUILD_TZ` for that date (this is what keeps 8 PM at 8 PM across DST). `locksAt =
   startsAt − lockMinutesBefore`. Create Signup rows (`standing ROSTER, response null`) for
   every roster-derived user. Skip dates that already have a raid for the series.
2. **Post.** Raids with `postedAt null` and `startsAt − now ≤ postAheadDays` → enqueue
   `raid.post`, set `postedAt`.
3. **Remind.** Raids posted, not locked, `startsAt − now ≤ 72h` and `remind72At null` →
   enqueue `raid.remind` with the ROSTER users whose response is null; set `remind72At`.
   Same at 24h.
4. **Lock.** `now ≥ locksAt` and status SCHEDULED → LOCKED, `lockedAt`, enqueue `raid.lock`.
5. **Close.** `now ≥ startsAt + durationMin + 60min` and status LOCKED → DONE, enqueue
   `raid.close`. Attendance can still be entered afterwards.
6. **Reserve reminder.** Only with `LOOT_ENABLED` on: posted SCHEDULED or LOCKED raids
   whose template has at least one loot-table item, in the two hours before reserves lock
   (`startsAt − 120 min`, independent of sign-up `locksAt`). Include the window's start,
   exclude its end; never catch up after reserves lock. With `remindReservesAt null`,
   conditionally set it and enqueue one `raid.reserves.remind` in the same transaction.
   Recipients are ACCEPT or TENTATIVE signups with a character, regardless of standing,
   missing HR or SR (officer-placed picks count for their holder). Compute ids at enqueue
   time. If nobody is owed, mark done without a job. Overlapping ticks cannot enqueue twice.
   Returned `reservesReminded` counts jobs enqueued, not empty evaluations.
7. **Nudge.** Applications PENDING for > 24h with `nudgedAt null` → enqueue
   `application.nudge`, set `nudgedAt`.
8. **Reconcile (once per hour).** For every raid and application with a thread id and
   status that implies archived/locked, enqueue `*.update` (the bot's update handler also
   fixes archive/lock/tag state). Cheap insurance against drift.

New roster members: when a user's `rank` becomes a roster rank while `inGuild`, `tick` step 1 also
adds them to every SCHEDULED future raid. Rank leaving the roster removes their unanswered
rows only; answered rows are kept.

---

## 7. Sign-up rules (site enforces, bot displays)

- Only `MEMBER` or `OFFICER` role may respond or bench. `SOCIAL` gets 403 and the bot replies
  "The calendar is view-only for social members."
- A ROSTER member may set ACCEPT / TENTATIVE / ABSENT any time before `locksAt`.
- A non-roster member pressing Accept/Tentative is placed on the BENCH with that response.
  Pressing Decline while not on the roster does nothing ("You're not on this roster").
- "Join bench" = BENCH + ACCEPT.
- After lock: members get 409 "Sign-ups are locked"; officers may still change anyone via the
  web (`setByUserId` recorded, shown as "set by X").
- Officers may move BENCH → ROSTER and back on the web at any time before DONE.
- Counts: composition bars count `ROSTER + ACCEPT` only. Tentative shows in counts but never
  in the bars.
- Every response records `source` (WEB or DISCORD). Shown on every row on the web.

---

## 8. Discord rendering

### `#applications` (forum, visible to Officer role + bot)
- Tags: `Raider`, `Social`, `Pending`, `Accepted`, `Declined`. Bot creates missing tags on
  startup and caches ids.
- Starter embed: title `Name · Class · Spec · Role`; one field per answer (long answers as
  full-width fields); footer "Submitted on the web". Buttons: **Accept** (green), **Decline**
  (red), **Open on web** (link). Buttons persist across restarts.
- Accept/Decline click → `POST /applications/:id/decision` → ephemeral confirmation. 409 →
  ephemeral with the reason. The visible changes come from the `application.decide` job.
- **Thread messages**: on `on_message` in an application thread, if the author is not the bot
  and the site says they're an officer, `POST /notes`. On edit/delete, PATCH/DELETE. Messages
  from non-officers are ignored (they can't see the channel anyway). Requires the
  **Message Content** privileged intent — enable it on the bot in the developer portal.
- Web notes arrive as `application.note.post` and are posted by the bot as
  `**Robert** (web) · text`. The bot ignores its own messages when ingesting.

### `#raid-signups` (text channel, read-only for members, chat in threads)
- Permissions: Guild Member — deny Send Messages, allow Send Messages in Threads,
  allow Add Reactions off. Bot — Send Messages, Create Public Threads, Send Messages
  in Threads, Manage Messages, Manage Threads.
- One message per raid. tick enqueues `raid.post` in `startsAt` order, so the channel
  reads chronologically. A raid created inside the `postAheadDays` window is posted at
  the bottom and its embed title is suffixed " · added late" (the site marks it
  `late: true` in the `raid.post` payload).
- The bot opens a thread on each post named exactly like the embed title minus the
  state marker, e.g. "Molten Core — Thu Nov 19". Reminders go in the thread.
- State lives in the embed, not tags: title prefix 🟢 Open / 🔒 Locked / ✅ Done /
  ❌ Cancelled, and embed colour teal / sand / green / red. Store nothing about state
  in the message itself; always re-render from `GET /raids/:id`.
- Embed body is unchanged from the previous spec: `<t:>` timestamps, monospace
  composition bars, six inline fields (Accepted, Tentative, Declined, Not answered,
  Bench, Locks at), footer "Updated <t:R> · Full roster, bench and who hasn't
  answered are on the web". No names on the embed.
- Buttons unchanged: **Accept** / **Tentative** / **Decline** / **Join bench** / **View
  roster** (link). Decline opens a modal with one optional "Reason" field. Every click
  gets an ephemeral reply stating the user's new state.
- `raid.close` and `raid.cancel` edit the message to a single compact line
  ("✅ Molten Core — Thu Nov 19 · 38 attended" or "❌ … · cancelled: <reason>"),
  remove all buttons, and archive the thread. The message is not deleted.

### `#apply` message
- Replace the two component buttons with **link buttons**: `Apply as a Raider` →
  `SITE_PUBLIC_URL/apply?path=raider`, `Apply as Social` → `SITE_PUBLIC_URL/apply?path=social`.
  Remove the modal flow. Keep the message text; add "You'll sign in with Discord on the site,
  so join the server first."

---

## 9. Site UI changes

1. **`/apply`** — public route, form only. Requires Discord sign-in. On load, check guild
   membership via the existing bot-token lookup; not a member → card with the invite instead
   of the form. `?path=raider|social` preselects the path control. One pending application per
   Discord ID (existing rule). Success → existing `/recruitment/submitted`. The form on
   `/recruitment#apply` becomes two buttons that link to `/apply?path=…`.
2. **Officer application view** — notes become a conversation: compact rows, avatar, name,
   source badge (Discord / Web), time; composer at the bottom; "synced with #applications" in
   the card header; link to the thread. Accept/Decline confirmation modal lists what will
   happen (status, tag, thread closed, DM, role change, roster entry).
3. **Templates & series (officers)** — `/officers/raids`: template table (editable), series
   list, "New series" form (template, weekday, time in guild zone, length, notes). Editing a
   series asks "This raid only" (detaches the instance) or "This and future raids".
4. **Calendar** — month grid + list toggle (list is the mobile default). Chips show
   `short · time`, a sand edge when the viewer has answered. Upcoming list beside the grid,
   sorted with "needs your answer" first.
5. **Raid detail** — viewer's own response control first; composition bars; roster grouped
   by role with Answer and Via columns; Bench card with "Move to roster"; "Hasn't answered"
   card with **Nudge in Discord** (enqueues `raid.remind`); "Answer for them" per row
   (officers); "Mark attendance" after the raid ends; Cancel with reason (officers).
6. **Roster editor** — lists everyone in the guild on Discord; a rank change (on a main, or the
   rank select on a member without one) enqueues `member.roles.sync` for the Raider, Trial and
   Social roles. Officer rank is read-only, it comes from the Discord Officer role.
7. **Set my main (Discord)** — the panel's "Set my main" button asks class, then spec (from
   `/classes`), then the raid role only when the spec fills two (Feral: tank or melee), then a
   modal for the first and second name (WoW Forever names are two parts, 12 letters each), and
   writes the main to the web roster through `PUT /members/:discordId/main`. The Discord class
   roles are still granted as before; the web form takes the two names too.
   A member who already has a main gets a **Keep <Class>** button beside the class menu, so the
   same character can change spec, raid role or name. When the web main is that class, the spec
   and raid role it has are marked "your current pick" (marked, not pre-selected: Discord sends
   nothing when a pre-selected option is picked again) and the name modal opens filled in, from
   `GET /members/:discordId`. That look-up is best effort; without it the flow runs unmarked.
8. **/recruitment (Discord)** — officers run it in the recruitment channel
   (`RECRUITMENT_NEEDS_CHANNEL_ID`): class, then spec (each spec shows its current status), then
   High / Medium / Closed. The bot writes it through `PUT /needs`.

---

## 10. Environment

Site (Vercel): `BOT_SHARED_SECRET`, `GUILD_TZ=America/Los_Angeles`, `DISCORD_ROLE_RAIDER`,
`DISCORD_ROLE_TRIAL`, `DISCORD_ROLE_SOCIAL`, `DISCORD_ROLE_GUEST` (beside the existing
`DISCORD_ROLE_OFFICER` and `DISCORD_ROLE_MEMBER`). Remove `BOT_WEBHOOK_URL`.

Bot (server env file): `DISCORD_TOKEN`, `GUILD_ID` (required by the sync now), `SITE_API_URL=https://www.bureauguild.com`,
`SITE_PUBLIC_URL=https://www.bureauguild.com`, `BOT_SHARED_SECRET`, `APPLICATIONS_FORUM_ID`,
`RAID_SIGNUPS_CHANNEL_ID`, `OFFICERS_CHANNEL_ID`, `ROLE_GUILD_MEMBER_ID`, `ROLE_GUEST_ID`,
`ROLE_RAIDER_ID`, `ROLE_TRIAL_ID`, `ROLE_SOCIAL_ID`, `ROLE_OFFICER_ID` (read only, for the snapshot),
`RECRUITMENT_NEEDS_CHANNEL_ID` (where `/recruitment` runs; blank allows any channel),
`POLL_SECONDS=5`, `TICK_SECONDS=60`, `SNAPSHOT_SECONDS=300`.

---

## 11. Done means

- An application submitted on the web appears as a forum post within 10 s. A note typed in
  the thread shows on the web within 10 s, and a note typed on the web appears in the thread
  within 10 s, attributed correctly. Accept on either side produces identical results on both,
  including the role change and the DM. Reopen works from both.
- Creating a Thursday series produces four raids with the correct `startsAt` across the
  November DST change (8 PM stays 8 PM Pacific). The post appears 14 days out. Clicking Accept
  in Discord updates the web within 10 s and the embed counts within 10 s. Responding on the
  web updates the embed. Lock at T−2h, reminders at 72 h and 24 h, close after the night.
- The bot restarted mid-way loses nothing: jobs resume, buttons still work.
- Both repos' Checks (listed in each `AGENTS.md`) pass; every PR reviewed.
