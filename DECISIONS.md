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
