# Audits — what is still open

Four separate audits used to live in `COUPLING.md`, `REVIEW.md`,
`FEATURES_AUDIT.md` and `AGENT_FINDINGS.md`. Each was a point-in-time report
and most of each has since been fixed, so what is left is one list of open
items and the two ideas worth reusing. The full reports are in git history.

## 1. The coupling states

A rule that is checked but not constrained means the solver is blind to it and
violates it at random. "Coupled or not" is too coarse; every rule is one of:

| State | Meaning | Consequence |
|---|---|---|
| **hard** | a CP-SAT constraint group — infeasibility, not cost | cannot fire on a generated plan; fires on hand-edited and imported ones |
| **construction** | the geometry pipeline cannot produce the violation | same: the rule is for the editor and for ResPlan import |
| **soft** | a term in the cross-topology key or the surrogate objective | fires when it loses a trade-off, and the trade-off is reportable |
| **uncoupled** | checked, and nothing anywhere can act on it | fires and persists; the loop cannot clear it, so it is noise |
| **data** | not a defect statement at all (`BYLAW.*_UNDECLARED`, `TYPO.ASSUMED`) | belongs in a different list |

Counted over the 100 implemented rules: 10 hard, 23 construction, 41 soft,
19 uncoupled, 7 data.

**Reading terms is not enough.** This audit read the solver's terms and put
`VASTU.ENTRANCE` in the *soft* column on the strength of `w_vastu`. Reading its
*outputs* showed that wrong, and the next item is why: a term can exist while
the variable it acts on is constant.

## 2. Open: the front door is not a decision variable

`solver.py`, in `_emit_plan`:

```python
ex0, ey0, ex1, ey1 = rects[ent]
if ey0 == Y0:                       # only if the entrance room touches the top edge
    place(w, (ex0 + ex1) // 2, FRONT_DOOR_W, "front_door")
```

The door is pinned to the `Y0` edge of the tiling. `road_facing` rotates the
envelope; the door does not move with it. Consequences, all still live:

* the front door was on the north wall of 89 of 89 suite plans;
* `VASTU.ENTRANCE` can never be satisfied or optimised, so the `vastu-*` suite
  cases are not testing what their names imply;
* when the entrance room does not touch `Y0`, **no front door is placed at
  all** and `GEO.NO_FRONT_DOOR` fires.

This is the largest open item in the file.

## 3. Open: three uncoupled rules that need a decision, not a fix

* **`NBC.PASSAGE_WIDTH` measures something else.** `min_clear_width` bounds a
  room's rectangle; the rule measures clear width between door thresholds. The
  solver can satisfy the constraint and fail the rule.
* **`DESIGN.KITCHEN_SINGLE_ACCESS` is unreachable by construction.** The door
  graph is a tree and no pass adds a cycle, so a kitchen gets a second door
  only as a side effect. Circulation loops, which real houses have, cannot be
  built.
* **Both `ZONE` rules are uncoupled.** The slicing tree has no zone-contiguity
  term, so public/private clustering is left to chance.

Each wants one of three answers, stated rather than left as a warning:
constrain it, demote it to a measurement, or delete it. A rule no mechanism can
satisfy and no user can act on is noise.

## 4. Open: the programme does not survive the solve

`bhk_programme` sets the master's target higher and weights it 1.35; the solver
spends the budget elsewhere and — before this work — nothing compared delivered
areas back against the programme's *intent*. Partly closed:
`DESIGN.LIVING_NOT_LARGEST`, `DESIGN.NO_ENSUITE_MASTER` and
`DESIGN.CIRCULATION_OVERSIZED` now exist. Still unwritten, in frequency order
from the n=4 pilot:

| Rule | What it would catch |
|---|---|
| `PROG.MASTER_NOT_PRIMARY` | the master is not the largest bedroom, or is not distinguished from the others by area |
| `PROG.ROOM_OUTSIDE_BAND` | a room grossly outside its `roomtypes.target_m2` band either way (a pooja at 8.7 m² and at 1.6 m² against a 1.0–5.0 band) |
| `PURPOSE.FOYER_NOT_AT_ENTRY` | a foyer that is an interior room — 53 of 89 plans |
| `PURPOSE.UTILITY_NOT_OFF_KITCHEN` | caught today only by the accident of there being no door |
| `REACH.WC_FROM_LIVING` | walking distance, which needs a primitive the code does not have |

The `PROG.*` ones are cheap: the data is already in `SolveResult.area_dev_pct`
and `roomtypes.target_m2`, and they need no new geometry primitive. The
`REACH.*` ones do.

## 5. The two methods worth reusing

**Tag every objection.** In a recall pass, each finding is *CAUGHT* (a rule
reports it), *SILENT* (a rule exists, its stated intent covers this, and it
does not fire — a logic gap) or *NO RULE* (nothing addresses it). The three
have different fixes and lumping them together hides that.

**State the reviewer's bias.** A model reviewing model-generated plans is
biased toward missing exactly what the generator missed, so every recall figure
from that setup is an *upper* bound on what an architect would give. Anchor on
things outside the reviewer's judgement — the rendered drawing, the NBC minima
and `roomtypes` bands already in code, `SPACES.md`'s five questions as a fixed
checklist — and say n.
