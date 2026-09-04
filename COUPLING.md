# 0a — the coupling audit

**Question:** of the defects the validator can detect, how many are wired to
something that can fix them? A rule that is checked but not constrained means
the solver is blind to it and will violate it at random.

**Method:** read the rule against the solver's actual mechanisms — the hard
CP-SAT constraint groups, the cross-topology penalty key, the topology
surrogate's objective weights, the door-assignment algorithm, and the
pre-solve envelope. Nothing was run; this is source reading.

**My prediction going in was that most errors would be uncoupled. That was
wrong,** and the way it was wrong is more useful than the prediction. The
mechanisms are richer than I expected; the problem is that two of the three
production paths don't switch them on.

---

## 1. Four coupling states, not two

"Coupled or not" turned out to be too coarse. Every rule sits in one of these:

| State | Meaning | Consequence for the score |
|---|---|---|
| **hard** | a CP-SAT constraint group; an infeasible model rather than a penalty | can never fire on a generated plan; fires on hand-edited and imported ones |
| **construction** | the geometry pipeline cannot produce the violation | same — the rule exists for the editor and for ResPlan import, not for us |
| **soft** | a term in the cross-topology key or the surrogate objective | can fire when it loses a trade-off, and the trade-off is *reportable* |
| **uncoupled** | checked, and no mechanism anywhere can act on it | fires and persists; the loop cannot clear it, so it is pure noise in the score |

A fifth, orthogonal: **data / disclosure** — 7 rules that are not defect
statements at all (`BYLAW.*_UNDECLARED`, `SITE_UNSPECIFIED`, `TYPO.ASSUMED`,
`BRIEF.RELATION_UNCHECKED`). They belong in a different list from defects.

## 2. The mechanisms that exist

Worth listing, because I under-rated them:

* **Hard constraint groups** — `max_area`, `min_area`, `min_clear_width`,
  `max_aspect_hard`, `door_width`, `pinned`. Infeasibility, not cost.
* **Cross-topology key**, with a stated priority order: habitable room with no
  exterior wall `2e7` > required adjacency unmet `1e7` > room with no door to
  circulation `5e6` > sole bath behind a bedroom `4e6` > wet room with no
  exterior wall `1e6`.
* **Surrogate objective weights** — `w_area`, `w_aspect`, `w_vastu`, `w_hub`
  (public core touches many rooms), `w_private` (private room acting as a
  corridor), `w_soft_adj`, `w_circ`.
* **Hard edge exclusions** — `NBC_FORBIDDEN` (kitchen–bathroom) and the
  caller's own `forbidden_adjacency` are removed from the door graph outright.
  *I suspected `forbidden_adjacency` was collected and dropped. It is not — it
  is applied at `solver.py:769`.*
* **Door assignment** — a star, not a minimum spanning tree, precisely so the
  public core is the hub. Wet rooms and pooja are leaves by rule. Returns
  `None` and asks for the next topology rather than routing traffic through a
  bedroom.
* **Pre-solve envelope** — setbacks, coverage, FAR and floors bound the
  buildable polygon *before* the tiling exists.
* **Glazing sizing** — windows sized to the NBC fraction and spread over as
  many exterior edges as it takes.

## 3. By family

| Family | hard | construction | soft | uncoupled | data |
|---|---|---|---|---|---|
| `GEO` (12) | 1 | 10 | 1 | 0 | 0 |
| `NBC` (19) | 7 | 3 | 8 | 1 | 0 |
| `BYLAW` (11) | 0 | 5 | 0 | 1 | 5 |
| `DESIGN` (27) | 0 | 2 | 15 | 10 | 0 |
| `TYPO` (4) | 1 | 0 | 1 | 1 | 1 |
| `TOPO` (4) | 0 | 2 | 1 | 1 | 0 |
| `ZONE` (2) | 0 | 0 | 0 | **2** | 0 |
| `SYNTAX` (4) | 0 | 0 | **4** | 0 | 0 |
| `VASTU` (9) | 0 | 0 | 7 | 2 | 0 |
| `BRIEF` (8) | 1 | 1 | 4 | 1 | 1 |
| **Total (100)** | **10** | **23** | **41** | **19** | **7** |

**The 18 uncoupled rules**, the ones nothing can fix:

`DESIGN.BEDROOMS_SCATTERED` · `DESIGN.DEAD_END_CIRCULATION` ·
`DESIGN.TOO_DEEP` · `DESIGN.ENTRANCE_NO_BUFFER` · `DESIGN.DINING_ABUTS_WC` ·
`DESIGN.WC_VISIBLE_FROM_DINING` · `DESIGN.KITCHEN_SINGLE_ACCESS` ·
`DESIGN.KITCHEN_FAR_FROM_PARKING` · `DESIGN.NO_WARDROBE_WALL` ·
`DESIGN.SINGLE_ASPECT` · `NBC.PASSAGE_WIDTH` · `TYPO.ENTRY_SEQUENCE` ·
`TOPO.NO_COMMON_BATH` · `ZONE.PRIVATE_FRAGMENTED` · `ZONE.PUBLIC_FRAGMENTED` ·
`VASTU.BRAHMASTHAN` · `VASTU.ENTRANCE` · `BYLAW.RWH_REQUIRED` · `BRIEF.ROOM_MISSING` (for a room `bridge` defers or drops)

**Correction, from `FLOORPLAN_ISSUES.md`.** I first put 8 of the 9 `VASTU`
rules in the *soft* column on the strength of `w_vastu` and
`RoomReq.vastu_zone`. That is wrong for `VASTU.ENTRANCE`, and the fuller
review found it by looking at outputs rather than at terms: **the front
door is on the north wall of 89 of 89 suite plans.** The cause is
`solver.py:1073` —

```python
ex0, ey0, ex1, ey1 = rects[ent]
if ey0 == Y0:                      # only if the entrance room touches the top edge
    place(w, ..., "front_door")
```

The door is hard-coded to the `Y0` edge of the tiling. `road_facing` rotates
the envelope; the door does not move with it. So the entrance position is
not a decision variable at all, `VASTU.ENTRANCE` can never be satisfied or
optimised, and the `vastu-*` suite cases cannot be testing what their names
imply. Worse, when the entrance room does not touch `Y0` **no front door is
placed at all** and `GEO.NO_FRONT_DOOR` fires.

The general lesson: this audit read the solver's *terms*. Reading its
*outputs* found a coupling failure the terms could not reveal, because the
term exists and the variable it acts on is constant. Both passes are needed.

Three of those are worth singling out:

* **`NBC.PASSAGE_WIDTH` is a different measurement from what is constrained.**
  `min_clear_width` bounds a room's *rectangle*; the rule measures clear width
  *between door thresholds* (`passage_clear_width_mm`). The solver can satisfy
  the constraint and fail the rule.
* **`DESIGN.KITCHEN_SINGLE_ACCESS` has no mechanism that could ever satisfy it
  deliberately.** The door graph is a tree — no pass adds a cycle. A kitchen
  gets a second door only as a side effect of another room choosing it as a
  host. Circulation loops, which real houses have, are unreachable by
  construction.
* **Both `ZONE` rules are uncoupled.** The slicing tree has no zone-contiguity
  term at all, so public/private clustering is left entirely to chance.

## 4. The finding that matters more than the count

**Two of the three paths that solve a plan pass no relational constraints at
all, then validate it with rules that assume them.**

| Caller | `required_adjacency` | `forbidden_adjacency` | `soft_adjacency` |
|---|---|---|---|
| `score.py` — offline suite runner | yes | yes | yes |
| `loop.py` — the closed repair loop | **no** | **no** | **no** |
| `service/app.py` `/generate` — what ships | **no** | **no** | **no** |

`score.py` calls `topology.solver_pairs(prog, scenario)` and hands all three to
`LayoutSpec`. `loop.py:161` and `service/app.py:130` construct `LayoutSpec` with
`programme`, `entrance_room` and `time_limit_s` and nothing else.

So on the two live paths the `1e7` required-adjacency term multiplies an empty
list, `w_soft_adj` weights nothing, and the only edge exclusion left is the
hard-coded kitchen–bathroom pair. **42 rules classified "soft" above are soft
only on the suite path.** On the service path a large part of that 42 collapses
into "uncoupled".

This also means the measured suite numbers in `PROGRESS.md` — 54/100 fully
passing, 72/100 with zero rule errors — describe a configuration that neither
the repair loop nor the deployed service runs.

## 5. Cross-check against a real report

The 11-room 3BHK on 30×40 came back with 0 errors and 14 warnings. Classifying
each by the table above:

| Finding | State |
|---|---|
| `DESIGN.SINGLE_ASPECT` ×5 | uncoupled |
| `DESIGN.ENTRANCE_NO_BUFFER` | uncoupled |
| `ZONE.PRIVATE_FRAGMENTED` | uncoupled |
| `ZONE.PUBLIC_FRAGMENTED` | uncoupled |
| `VASTU.BRAHMASTHAN` | uncoupled |
| `TYPO.FORBIDDEN_ADJACENCY` (pooja opens into kitchen) | soft — but the pair was never passed on this path |
| `VASTU.POOJA`, `VASTU.MASTER_BEDROOM` | soft, lost the trade |
| `TYPO.ASSUMED` | disclosure |
| `BYLAW.SITE_UNSPECIFIED` | data |

**9 of 14 are uncoupled, and a 10th is a relational rule whose constraint was
never populated.** Only 2 of 14 are a solver that considered the trade-off and
chose otherwise. The residual findings on a solved plan are almost exactly the
uncoupled set — which is what you would predict, because the coupled ones get
optimised away and the uncoupled ones cannot be.

## 6. What this changes about the sequence

The prediction I gave you was "coupling, not coverage". The audit says something
narrower and cheaper:

1. **Populate the relational terms on the two live paths.** `loop.py` and
   `service/app.py` should resolve a scenario and call
   `topology.solver_pairs`, the way `score.py` already does. This is a handful
   of lines and it moves ~40 rules from effectively-uncoupled to soft on the
   paths that matter. Nothing else on the list has this ratio of effect to
   effort.
2. **Then decide what to do with the 18.** Three options per rule, and the
   choice should be explicit rather than left as a persistent warning:
   *constrain it* (zone contiguity is a real objective term the slicing tree
   could carry); *demote it to a measurement* (`SINGLE_ASPECT` is a property to
   report, not a defect to fix); or *delete it* (a rule no mechanism can
   satisfy and no user can act on is noise).
3. **Only then write new rules.** Adding to the 18 makes the report longer and
   the plans no better.

One consequence for the protocol discussion: `channel` should not be an
authored field. It is derivable — from the constraint groups, the key, the
objective weights and the caller's `LayoutSpec`. Deriving it means the report
can say *"this finding has no fixer on this path"*, which is a far more useful
thing to tell a user than a severity.
