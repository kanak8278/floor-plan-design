# Agent findings

One line per finding, newest last. Written by `tests/probe_agent.py` runs plus
reading the replies. `tests/probe_agent.py --help` explains the outcomes.

Severity: **BUG** wrong behaviour · **LIMIT** the vocabulary cannot express it ·
**UX** it works but reads badly · **PERF** slow enough to notice ·
**INHERITED** pre-existing on `main`, not introduced by the chat work.

## Fixed while testing

- BUG (fixed) Minted room ids diverged across languages: each side hashed the centroid in its own units (mm vs cm), so the same room got two ids. Caught by the cross-language conformance test, not by reading.
- BUG (fixed) `reconcileRooms` documented "later entries win ties" but compared with `<=`, so a stale unnamed room beat the freshly named one and the plan kept reading "Room 1" while the chat said otherwise.
- BUG (fixed) Room detection was cached on wall geometry alone, so a rename did not invalidate it and the canvas kept the old labels.
- BUG (fixed) `adopt` replaced a document the service already held while keeping its transcript, so the assistant remembered edits the document had lost — indistinguishable from hallucination.
- BUG (fixed) The IR<->Project adapter renamed the editor's floor on every round trip (`f9k2p1` -> `floor-f9k2p1`), which would break `activeFloorId` and every storey reference.
- BUG (fixed) Four design-level fields (`name`, `description`, `created_at`, `updated_at`) came back as their derived defaults rather than empty, so a document did not survive the projection it is stored as.
- BUG (fixed, upstream) `FloorPlanCanvas.onKeyDown` called `preventDefault()` on `Space` before its form-field guard, so no text field in the app could accept a space — including the room-rename box.
- UX (fixed) The agent spent two commands on `room_class='bedroom'` when it meant `category='bedroom'`. `room_class` is only OpenPlan3D's four-value floor bucket. The catalogue now says so and the rejection names the right param.

## Inherited from main, not chat-agent work

- INHERITED `tests/test_integration.py` fails identically on `main`: 3 of 4 cases carry validator errors (`NBC.VENTILATION_HABITABLE`, `DESIGN.BEDROOM_THROUGH_TRAFFIC`, `SYNTAX.LIVING_NOT_CORE`, `SYNTAX.PRIVATE_ROOM_INTEGRATED`). The new DESIGN/SYNTAX/NBC checks are stricter than the solver currently satisfies.
- INHERITED 8 tests fail identically on `main`: 5 in `test_rules.py` (`test_open_plan_kitchen_warns_but_does_not_error`, `test_bedroom_behind_an_arch_but_with_its_own_door_only_warns`, `test_doorless_store_only_warns`, `test_false_positive_rate_on_real_plans`, `test_latency_budget_on_a_ten_room_plan`) and 3 in `test_solver.py` (`test_2bhk_on_20x30_reports_the_band_argument`, `test_pinned_walls_survive_a_resolve_and_unpinned_ones_do_not`, `test_solve_time_percentiles`).
- INHERITED `test_integration.py` is a script (`sys.exit(1)` at import), so it aborts pytest collection for the whole run rather than failing as one test.
- INHERITED `test_rules.py::test_false_positive_rate_on_real_plans` asserts median wall-clock latency under 10 ms and fails under concurrent CPU load. A timing bound inside a correctness test is a tripwire.
- INHERITED `PLAN.md` still documents "Python service — STATELESS ... the server never owns it", which the document service now contradicts. The stateless endpoints (`generate`, `validate`, `render`) are untouched and still stateless; the document is not.
- INHERITED `svelte-check` reports 6 errors in `BuildPanel.svelte`: `Tool` is typed without `'measure'`/`'annotate'` but the panel compares against both, so those tools are set through a path the type does not know about.
