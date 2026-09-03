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

## Found by probing (`tests/probe_agent.py`, 3 examples x 15 probes)

- BUG **A wall move tears the wall graph and destroys every room.** `move_wall_parallel`/`move_wall_by` relocate one wall's endpoints and leave the endpoints of the walls sharing those vertices behind, so the corners open, the ring stops being a closed face, and every room on the storey loses its identity, name and category. The command reports success. Pinned in `test_KNOWN_BUG_moving_a_wall_outward_destroys_every_room`. Fix is a graph-aware move that drags connected endpoints — the "IR mutation layer mirroring project.ts" `loop.py` already notes is missing.
- BUG An inward wall move survives only by accident: the perpendicular walls happen to overrun the moved wall so GEOS re-nodes the crossings. Do not read the inward case as the operation being safe.
- BUG (harness) The first `move/wall` probe asserted only "did a wall move", so it PASSED while destroying the document. A probe over a mutating system has to assert what must be *preserved*, not just what changed.
- BUG (harness) Probes share one cumulative document, so the wall-move damage poisoned every later probe in the run: most of the 8 refusals were the agent correctly declining to work on a genuinely broken plan. `--fresh-document` added. Attribution in a mutating harness needs isolation by default.
- LIMIT `rooms_gone` is reported but the agent has no command to *restore* a destroyed room, and no way to reject its own edit after seeing the damage. It can only narrate it. A `revert_last`/`undo` command in the symbolic vocabulary would let it back out.
- UX The plan digest gives each wall a length and an orientation but never says whether it is exterior, nor which two rooms it separates. The agent reconstructs both every turn from room wall-id lists ("w0 is the only one of those that faces outside") and gets it right, but it is paying reasoning for something the document already knows.
- UX A `move_opening` to a named fraction can silently re-plumb circulation: the agent caught that moving `o0` to the quarter point would make the living room open into the study and strand the hall, and refused. Correct, but the vocabulary has no way to express "move it within its current room pair", which is what the user usually means.
- GOOD The refusals are specific and checkable: they cite opening ids, positions along walls, clear widths, and the rule that would break. Three of them caught defects in my probe wording rather than doing the wrong thing.
- GOOD Read-only questions never mutated the document across 6 probes, and spec-level ops correctly changed nothing until a re-solve.
- PERF A probe averages ~35 s wall clock at effort `high` with 0-2 tool round trips. 45 probes took ~26 minutes.
