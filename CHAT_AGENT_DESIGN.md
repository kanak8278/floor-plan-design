# Integrated chat agent — design contract

Branch `chat-agent`. Goal: a floor-plan editor where a conversation and a mouse
edit the **same** document, where every change (from either) is visible in the
chat as a short line, and where that line is what the model reads next turn.

Not a bolt-on. The chat is not a sidecar that proposes JSON at the editor; the
editor and the agent are two clients of one authoritative document.

## Decisions taken (2026-09-04)

1. **Server-authoritative, event-sourced.** A Python service owns the document.
   Mouse and model both emit `Command`s into one ordered log. The server applies
   them to the IR and returns a projection, validator findings, and one
   human-readable event. Divergence is impossible because there is one applier.
2. **FastAPI service + SvelteKit BFF proxy.** SvelteKit `+server.ts` routes
   proxy `/api/*` to the Python service. One origin, no CORS, and the Anthropic
   key never reaches the browser.
3. **Tabbed right dock** — `Chat | Properties | Layers` in one collapsible
   VS-Code-style dock, so chat gets the full pane width.
4. **Foundation first.** Stable room identity and a lossless IR land before any
   chat UI. Everything above them is untrustworthy otherwise.

## Why the foundation comes first: two defects that break chat

### Room identity is derived and unstable

`vendor/openPlan3D/src/lib/utils/roomDetection.ts:228` mints room ids as
`` `room-${roomCount}-${Date.now()}` ``. Rooms are recomputed from wall-graph
cycles on every wall change, and a name/texture survives only when the room's
*exact wall-id set* is unchanged (`FloorPlanCanvas.svelte:851-873`). Split a
wall, add a wall, or move one, and "Master Bedroom" becomes "Room 3" with a new
id.

A chat agent's entire vocabulary is room names. Chat history references rooms
from ten edits ago. Derived identity cannot carry either.

**Fix:** rooms become persisted first-class entities with stable ids.
`detectRooms()` is demoted from *author* to *proposer*: its output is reconciled
against persisted rooms by geometric match (centroid containment + area
overlap), and only genuinely new faces get new ids.

### The IR↔Project adapter is one-way

`src/fpeval/project.py:from_project()` drops columns, guides, measurements,
dimension annotations, text annotations, groups, entourage, background image,
per-wall colours/textures, `curvePoint`, and every floor except the active one.

That was correct for what it was verified on — one-way conversion of plans we
generated ourselves (`IR -> Project -> IR`, 17,000/17,000 identity). It is not
a bridge for live editing: the first agent edit would delete the user's second
storey and their dimension annotations.

**Fix:** the IR becomes a superset of the editor's model, and the round-trip
test runs over *edited* projects carrying every field, not over our own output.

### On the word "lossless"

`Project` is float centimetres; the IR is integer millimetres (DECISIONS.md #1).
Arbitrary floats do not survive a trip through integer mm, so "lossless" here
means **lossless modulo one declared quantisation**: values are snapped to 1 mm
on entry to the IR, the snapped value is canonical, and the round trip is
*idempotent* from then on. The test asserts idempotence after one pass, not
byte equality with pre-quantisation input. Anything else would be a lie, and
the quantisation is desirable anyway — it is what kills float drift at joins.

## Command algebra

One vocabulary, two families. The split is load-bearing.

| Family | Authorable by | Carries coordinates | Examples |
|---|---|---|---|
| **symbolic** | agent and user | never | `set_room_area`, `move_wall_parallel`, `add_door(wall, position)`, `split_wall(wall, t)` |
| **direct** | user only | yes | `drag_wall_endpoint`, `move_furniture`, `place_column` |

`src/fpeval/llm.py` already has the symbolic half: `OP_TABLE` (19 ops, spec-level
vs geometry-level), `PatchOp.validate()` with referential checks against a live
spec *and* plan, `_BANNED_PARAM_KEYS` rejecting absolute coordinates, and
`to_editor_call()` naming real store functions. That ban stays and applies to
`source == "agent"`. Manual commands may of course carry coordinates — hence the
second family rather than a loophole in the first.

Every command yields an `Event`:

```
Event { seq, source: user|agent, op, summary, refs: [room_id|wall_id...], at }
```

`summary` is the chat bullet ("Middle bedroom 3.6 × 3.9 m", "Dining table
added"). It is generated from the command, never written at the call site, or
the two will drift.

## Conflict handling

Agent patches are computed against `seq = N` and may arrive at `seq = N + k`.
The server re-validates each op against current state at apply time —
`PatchOp.validate(spec, plan)` already does exactly this — and applies the ops
that still make sense. `PatchBatch.rejected` (op + reasons, already implemented)
becomes the user-facing explanation:

> Widened the master bedroom. Skipped the door move — you deleted that wall.

Interactivity survives because the client applies optimistically and only
commits at gesture boundaries. Those boundaries already exist in the store:
`moveWallEndpoint` deliberately does not snapshot, and `beginDrag` /
`commitFurnitureMove` mark the commit points. A drag stays local at 60 fps and
produces one command on release.

## Agent loop

Python, manual tool-use loop rather than the SDK Tool Runner: every step must be
persisted and streamed as a domain event, and turns must be interruptible.

- `claude-opus-5`, `thinking: {type: "adaptive", display: "summarized"}` so the
  pane can show reasoning, streaming, `effort: "high"`.
- **Caching layout.** Stable prefix — system prompt + frozen, sorted tool
  definitions — behind a cache breakpoint. Transcript next. The volatile
  per-turn payload goes in as a **mid-conversation system message**
  (`{role: "system"}` inside `messages`, supported on Opus 5): it preserves the
  cached prefix and carries operator authority, so plan state cannot be spoofed
  from a user message.
- **What the per-turn payload contains:** the current plan digest plus the
  events since the last assistant turn — *not* the whole event history. State
  is for correctness; history is for resolving "make it bigger". Unbounded
  bullets would eat the window.
- Tools: read-only (`get_plan`, `get_findings`, `get_area_statement`,
  `explain_rule`, `list_catalogue`) run in parallel; write tools
  (`apply_patch`, `resolve_layout`, `furnish_room`) take a `seq`.
- CP-SAT solve is p50 2.2 s — inline, with a streamed progress event. Work over
  ~10 s (furnishing, multi-storey re-solve) becomes a job row and polls.

## Persistence

Event-sourced with periodic snapshots. Tables: `designs`, `design_snapshots`,
`commands`, `events`, `messages`, `jobs`. SQLite first, Postgres shape.

This gives undo, redo, time travel, and audit from one mechanism, and the
existing `UndoHistoryPanel` / `jumpToUndoStep` map onto it directly.

`localStore` stays behind the existing `DataStore` interface as the
offline/anonymous path; `remoteStore` is added alongside it.

## Housekeeping in our fork

- `src/lib/firebase.ts` ships upstream's Firebase config and initialises
  analytics against *their* project. Cut it.
- `src/lib/stores/aiKeys.ts` stores Gemini/OpenAI keys in `localStorage` and is
  imported by nothing. Delete it — keys live server-side now.

## Stages

Each is independently mergeable.

1. **Foundation — DONE.** See "Stage 1 as built" below.
2. **Command bus.** One `dispatch()` in the store; every mutator routed through
   it; every command emits an event; `EventLog` store; `apply_command` on the IR
   in Python; conformance test — same command sequence, same state hash.
3. **Backend.** FastAPI design service, DB, SSE, BFF proxy, `remoteStore`.
4. **Agent loop.** Tool set, caching layout, streaming, seq conflict handling.
5. **Chat UI.** Right dock with tabs, event feed, diff cards with accept/undo.
6. **Furnishing** rides on this as a tool (the other open task: LLM picks
   catalogue ids from a closed enum, a relational solver owns coordinates).

## Stage 1 as built

### Identity

`vendor/openPlan3D/src/lib/utils/roomIdentity.ts` and its twin
`src/fpeval/roomid.py`. A room owns an **anchor** — a point inside it, recorded
when it is first named — and whichever detected face contains that point is
that room. `roomDetection.ts` is untouched, so the byte-for-byte vendor parity
check in `tests/js/verify.ts` still means what it says; reconciliation happens
downstream and overwrites the ids anyway.

Behaviour worth stating:

- A room that matches no face is **held, not deleted**. Mid-drag the wall loop
  is briefly open and every face vanishes; a name must not be a casualty of a
  gesture that has not finished.
- Merging two rooms reports the loser in `unmatched` rather than silently
  absorbing it, so the chat can say *"Bedroom 2 no longer exists"*.
- Geometry (`wall_ids`, `area`, `polygon`) always comes from the current face.
  Only identity is carried.
- Ids are minted by FNV-1a over the centroid **in millimetres on both sides**.
  They were originally hashed in each side's own units, which produced
  different ids for the same room — caught by the conformance test, not by
  reading the code.

### Fidelity

`ir.py` grew a `Presentation` block, `Column`, and a `Design` wrapper so `Plan`
stays single-storey and every rule, solver, and renderer written against it
keeps working. `project.py` is now two-way across every storey and every field.

The subtle part is derived defaults. A bathroom's floor texture is *derived*
from its category, so the IR has two states ("explicitly ceramic-white" and
"derived, which happens to be ceramic-white") where Project has one field. The
sidecar records which values were overrides; for editor-authored documents with
no sidecar entry we fall back to comparing against the derived value, and the
residual ambiguity — an override equal to its own default — is invisible
because both states emit the same Project field.

### Measured

- `tests/test_project_roundtrip.py` — 26 tests. One per field family, so a
  regression names what it broke. Includes a v1-sidecar migration case and a
  test that a generated plan does *not* acquire editor defaults on the way out.
- `tests/test_room_identity.py` — 15 tests, two of which drive the TypeScript
  rule through `tests/js/reconcile_driver.ts` and compare answers.
- `tests/js/roomIdentity.test.ts` — 10 tests. The last asserts the **old** rule
  fails the wall-split case, so the suite is known to discriminate.
- 1,000 real ResPlan plans: 0 `ir_identity` failures, 0 idempotence failures,
  0 dataclass-equality failures, ~7 ms/plan.

### The duplication, named

The reconciliation rule now exists twice, in TypeScript and in Python. That is
the standing risk of a server-authoritative document with an optimistic client,
and it is held down by the conformance test rather than by discipline. If the
rule grows, the conformance cases grow with it — that is the deal.

## Constraints inherited

- DECISIONS.md #1: integer mm is authoritative; `Project` is a lossy projection.
- DECISIONS.md #6: the LLM emits specs and symbolic patches; solvers emit
  coordinates. The `source`-gated command families are how that is enforced
  once manual edits share the same log.
