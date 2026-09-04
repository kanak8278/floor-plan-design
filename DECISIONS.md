# Load-bearing decisions

1. **Integer millimetres** in the IR. CP-SAT wants integer domains; float cm drifts at wall joins.
   OpenPlan3D's `Project` is float cm, so it is a *lossy projection* — the IR stays authoritative.
2. **Wall-network-first.** Walls are centrelines + thickness (a planar straight-line graph);
   rooms are derived faces; openings are parametric `(wall_id, position 0..1, width)`.
   This is what makes prompt-edits and mouse-edits share one substrate.
3. **ResPlan inversion goes through the room tiling, not the wall mass.** Measured: skeletonising
   the `wall` MultiPolygon gives median IoU 0.37 with 17% slivers. Room-boundary union gives ~1.000.
4. **ResPlan is scale-ambiguous.** Coordinates are not metric; `wall_depth` is the only physical
   anchor (implies ~226 mm walls). Absolute NBC area checks are therefore NOT testable on ResPlan;
   topology, adjacency, and relative areas are.
5. **Verifiable rewards come from the validator, not from ResPlan.** 5 of 6 reward terms need no
   dataset. ResPlan tests the plumbing (IR fidelity), never plan quality.
6. **LLM emits specs and symbolic patches; solvers emit coordinates.** Never let the model write
   geometry directly.
7. **Server-authoritative, event-sourced, one applier.** Mouse and model both emit `Command`s into
   one ordered log; the server applies them to the IR and returns a projection, findings, and one
   human-readable event. Divergence between the two clients is impossible because there is only
   one applier. The chat is not a sidecar proposing JSON at the editor — both are clients of one
   document.
8. **Room identity is persisted, not derived.** The editor mints ids from wall-graph cycles and a
   name survives only while the room's exact wall-id set does, so splitting or moving one wall
   turned "Master Bedroom" into "Room 3". An agent's whole vocabulary is room names and its history
   references rooms from ten edits ago, so derived identity cannot carry either. `detectRooms()` is
   demoted from author to proposer: its output is reconciled against persisted rooms by geometric
   match, and only genuinely new faces get new ids.
9. **The IR is a superset of the editor's model.** `from_project` once dropped columns, guides,
   measurements, annotations, groups, entourage, background image, per-wall colours, `curvePoint`
   and every floor but the active one. That was sound for one-way conversion of plans we generated,
   and wrong as a bridge for live editing — the first agent edit would delete the user's second
   storey. Round-trip tests run over *edited* projects carrying every field, not over our own
   output. "Lossless" is bounded by #1: arbitrary floats do not survive integer mm.
