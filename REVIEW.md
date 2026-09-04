# 0b — recall pass (pilot, n=4)

> **Superseded by `FLOORPLAN_ISSUES.md`**, which does the same job properly:
> all 89 suite plans, a visual pass on 35 and a geometric pass on all 89 parsed
> from the SVG room polygons. It confirms every pattern below at scale and finds
> more — master with no attached bath 79/89, foyer not at the entrance 53/89,
> kitchen-bedroom door 22/89, over 20% of carpet in circulation 10/89. Keep that
> as the recall instrument. This file is retained only for the tagging sheet
> (CAUGHT / SILENT / NO RULE), which is the reusable part.

**What this measures.** False negatives: things wrong with a plan that the
validator does not report. The false-positive rate is already measured per rule
against 1,500 real ResPlan plans with a per-rule budget in the tests. Nothing
measured recall, and all four of the defects that prompted this work were
recall failures.

**Who reviewed.** I did, because no architect is available. State the bias
plainly: **a model reviewing model-generated plans is biased toward missing
exactly what the generator missed.** To reduce that I anchored on three things
outside my own judgement rather than on taste — the rendered drawing (looking
at it, not reading the door graph), the `roomtypes` target bands and NBC minima
already in the code, and `SPACES.md`'s five questions as a fixed checklist.
Treat this as a lower bound on the gap, not a measurement of it.

**Protocol.** Eight programmes across the size bands, solved with the relational
terms on, rendered to `out/review/*.svg`, rasterised, and read as drawings.
Six solved; `D_30x50_3BHK` and `G_30x40_2BHK` returned INFEASIBLE on all 400
ranked topologies, which is a separate question. Four were reviewed in full.
Every objection is tagged:

* **CAUGHT** — a rule reports it.
* **SILENT** — a rule exists, its own stated intent covers this, and it does not
  fire. A logic gap, not a coverage gap.
* **NO RULE** — nothing in the 100 addresses it.

---

## A_20x30_1BHK — reported: 0 errors, 8 warnings

341 sqft carpet, 5 rooms.

| # | Objection | Tag |
|---|---|---|
| A1 | `HALL` is 29 sqft (2.7 m²) with one door, serving one room. **8.5% of the entire carpet area is leftover space the filler created.** | CAUGHT as `DESIGN.DEAD_END_CIRCULATION`, but as a `warn` at weight 0.5 — the honest finding is "delete this room", not "it reads as leftover" |
| A2 | Kitchen 5.7 m² against an NBC floor of 5.0 — legal by 0.7 m², and 2032 mm wide | not a defect; noting the margin |
| A3 | Wall thickness 230 mm here against 115 mm on every other plan in the set | NO RULE — no consistency check on wall thickness across a set |

## C_30x40_3BHK — reported: 0 errors, 13 warnings

781 sqft carpet, 11 rooms, `OPTIMAL`.

| # | Objection | Tag |
|---|---|---|
| C1 | **`MASTER BEDROOM` is 88 sqft. `BEDROOM 2` is 137 sqft.** The master is the smallest bedroom, tied with Bedroom 3. `bhk_programme` gives it a larger target and weight 1.35; the solver traded it away and nothing noticed. | **NO RULE** |
| C2 | **The en-suite went to Bedroom 2, not the master.** Bathroom 2 (43 sqft) opens off Bedroom 2; the master has no attached bath. | **NO RULE** |
| C3 | The largest room in the house is a bedroom (137 sqft) — larger than Living (102) and Dining (114). The area hierarchy is inverted. | **NO RULE** |
| C4 | `MASTER BEDROOM`'s only door opens off the `DINING` room. | NO RULE — `DESIGN.BEDROOM_OFF_LIVING` covers living; no equivalent for dining, and no `Pref` entry either |
| C5 | `BEDROOM 2` is 16'-7" × 8'-3" — aspect 2.0 at 2515 mm wide. Legal, and the worst-shaped room is also the largest. | not a defect on its own |
| C6 | Living and Dining are separated by the 20 sqft `HALL` | CAUGHT as `ZONE.PUBLIC_FRAGMENTED` |

## F_50x80_5BHK villa — reported: 2 errors, 30 warnings

2,368 sqft carpet, 18 rooms, `FEASIBLE`. The richest case.

| # | Objection | Tag |
|---|---|---|
| F1 | **`HALL` is 495 sqft / 45.9 m² — 15'-0" × 33'-0". That is 21% of the carpet area, in one corridor, and it is the largest room in the house** (Living is 300 sqft). `LayoutSpec.filler_min_m2 = 3.0` sets a floor on filler and no ceiling. | **NO RULE** — this is gap #129 `REACH.CIRCULATION_SHARE`, and it is not a marginal 20%, it is a 33-foot corridor |
| F2 | **`MASTER BEDROOM` is 100 sqft — the smallest of five bedrooms. `BEDROOM 3` is 220 sqft.** The master is 45% of the largest bedroom. | **NO RULE** (same as C1; systematic, not incidental) |
| F3 | The master bedroom has two doors — from `LIVING` and to `FOYER` — so it is a route between them | CAUGHT, almost certainly one of the 2 errors (`DESIGN.BEDROOM_THROUGH_TRAFFIC`) |
| F4 | The master has no attached bath; the largest bath (Bathroom 4, 63 sqft) is a common bath off the foyer | **NO RULE** (same as C2) |
| F5 | The master bedroom sits directly at the front door — the most public position in the plan — while Bedroom 3 at 220 sqft is also front-facing. Privacy gradient inverted in *area* as well as depth. | partly CAUGHT by `SYNTAX.*`; the area dimension is NO RULE |
| F6 | **`FOYER` is 122 sqft and sits in the plan's interior, one row behind the front door.** Arrival goes front door → Living or Master → Foyer. A foyer that is not the first space inside the door is a passage. | **NO RULE** — gap #101 `PURPOSE.FOYER_NOT_AT_ENTRY`, confirmed flagrant |
| F7 | **`UTILITY` is at the bottom-right; `KITCHEN` is at the bottom-left, ~30 feet away across the Hall.** `Pref("kitchen","utility",1.0,"direct")` is a *required* adjacency. | CAUGHT as `TYPO.MISSING_ADJACENCY` — but only because there is no door. A long passage joining them would pass. |
| F8 | **`POOJA` is 18 sqft / 1.6 m², wedged in the service corner between `STORE`, `UTILITY` and two toilets, and it shares a wall with `BATHROOM 2`.** `typology._COMMON` says "a shrine must not share a **wall** or door with a toilet" and `check_typology`'s `separate` branch tests `joined()` — doors only. | **SILENT** — the rule's own stated intent covers walls; the implementation does not |
| F9 | `STORE` is 19 sqft / 1.8 m² — the only store on a 4,000 sqft plot | NO RULE (see H2: no band check in either direction) |
| F10 | `SITOUT` is on the left facade beside Bathroom 4, not at the arrival point | **NO RULE** — gap #106 `PURPOSE.SITOUT_NOT_AT_ARRIVAL` |
| F11 | The 300 sqft `LIVING` has no WC without crossing the `FOYER`; three of four baths are on the opposite side | **NO RULE** — gap #121 `REACH.WC_FROM_LIVING`, the user's original complaint, reproduced |

## H_40x50_3BHK — reported: 5 errors, 19 warnings

1,191 sqft carpet, 14 rooms, `OPTIMAL`.

| # | Objection | Tag |
|---|---|---|
| H1 | **`DINING` (middle-left) and `KITCHEN` (top-right) are at diagonally opposite corners.** | CAUGHT as `TYPO.MISSING_ADJACENCY` — again only because no door exists |
| H2 | **`POOJA` is 93 sqft / 8.7 m² — larger than the `DINING` room (88 sqft) and nearly half the `LIVING`.** `roomtypes` gives pooja a target band of (1.0, 5.0) m². Nothing checks a room grossly *above* its band. | **NO RULE** — and note F8 is the same rule failing downward at 1.6 m² |
| H3 | **`MASTER BEDROOM` has a door into the `KITCHEN`.** `_EDGE_PEN` prices kitchen–bedroom at 7 and the solver paid it. No `Pref` entry, no rule. | **NO RULE** |
| H4 | `SITOUT` is 20 sqft / 1.8 m², 3'-4" (1016 mm) deep, in the plan's interior between `BATHROOM 1` and `UTILITY` | **NO RULE** — gap #107 `PURPOSE.SITOUT_TOO_SHALLOW`; nobody sits in a 1 m-deep interior alcove |
| H5 | `HALL` is 190 sqft = 16% of carpet | NO RULE (same as F1) |
| H6 | `MASTER BEDROOM` 115 sqft against Bedroom 2 at 113 and Bedroom 3 at 111 — the master is not distinguished at all | NO RULE (same as C1) |
| H7 | `KITCHEN` → `UTILITY` not adjacent; `STORE` far from the kitchen | CAUGHT (`TYPO.MISSING_ADJACENCY`, `DESIGN.STORE_FAR_FROM_KITCHEN`) |

---

## Tally

23 objections across 4 plans.

| Tag | Count |
|---|---|
| CAUGHT | 6 |
| SILENT (rule exists, does not fire) | 1 |
| **NO RULE** | **13** |
| Not a defect / noted only | 3 |

**Recall on this sample: 6 of 20 real objections, or 30%.** Every one of the six
that was caught is a *door-graph* finding — a missing door or a fragmented
touching-cluster. Not one area, proportion, position or distance objection was
caught, because no rule in the 100 measures any of those things.

The one-sentence version: **on the villa, the master bedroom is the smallest of
five, is a corridor between two public rooms, has no attached bath, and sits at
the front door; the foyer is buried mid-plan; the utility is thirty feet from
the kitchen; and 21% of the carpet area is a single 33-foot corridor — and the
validator reports two errors.**

## What the review adds to the gap list

Five rules that are **not** among the 63 in `RULES.md`, ranked by how often they
fired here:

| New ID | Rule | Seen in |
|---|---|---|
| `PROG.MASTER_NOT_PRIMARY` | the master bedroom is not the largest bedroom, or is not distinguished from the others by area | C, F, H — **3 of 4** |
| `PROG.ROOM_OUTSIDE_BAND` | a room grossly outside its `roomtypes.target_m2` band in either direction (pooja at 8.7 m² and at 1.6 m² against a (1.0, 5.0) band) | F, H — 2 of 4 |
| `PROG.MASTER_NO_ENSUITE` | the master has no attached bath while another bedroom does | C, F — 2 of 4 |
| `PROG.AREA_HIERARCHY_INVERTED` | the largest room in the house is neither the living nor a deliberate hall | C, F, H — 3 of 4 |
| `ACCESSGRAPH.BEDROOM_INTO_KITCHEN` | a bedroom door opens into the kitchen | H — 1 of 4 |

And it **confirms as frequent** five gaps already listed, which is the more
useful result — it turns a list written from codes and papers into a ranking:

1. **#129 `REACH.CIRCULATION_SHARE`** — 21%, 16%, 8.5% of carpet in filler halls. The most expensive defect in the set and completely unreported. Promote to first.
2. **#101 `PURPOSE.FOYER_NOT_AT_ENTRY`** — flagrant on the villa.
3. **#103 `PURPOSE.UTILITY_NOT_OFF_KITCHEN`** — 2 of 4, and currently caught only by the accident of there being no door.
4. **#107 / #106 `PURPOSE.SITOUT_*`** — a 1.8 m² interior "sitout" twice.
5. **#121 `REACH.WC_FROM_LIVING`** — reproduced on the villa exactly as originally described.

## Two things this changes

**The `PROG.*` family is new and it is the highest-frequency finding in the
review.** It is not about topology or distance — it is about whether the
*programme* survived the solve. `bhk_programme` sets the master's target higher
and weights it 1.35; the solver spends that budget elsewhere and no check
compares the delivered areas back against the programme's *intent*. Four of the
five new rules are this. That is cheap to build: the data is already in
`SolveResult.area_dev_pct` and `roomtypes.target_m2`, and it needs no new
geometry primitive at all — unlike `REACH.*`.

**Every caught objection was a door-graph finding.** The validator has one good
sense and is blind in the others. Area, proportion, position and walking
distance are all unmeasured, and between them they account for 13 of the 20 real
objections here.

## Caveats

* n=4 reviewed, n=6 solved, n=8 attempted. A pilot. The tagging sheet is the
  reusable part; scaling to 50 is mechanical once someone can look at drawings.
* Reviewed by the same class of system that generated them. The 30% recall
  figure is an **upper** bound on what a real architect would give.
* Two of eight programmes were INFEASIBLE. That may be correct — a 3BHK with
  three baths, pooja, utility and a store on 30×50 is genuinely tight — but it
  was not investigated and it is not counted here either way.
