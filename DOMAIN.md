# Domain knowledge: what to borrow, what to build

Survey done because I had been inventing design rules from first principles when
there is established theory and, in one case, a usable standard.

---

## 1. What exists — verified, not assumed

| Candidate | Verdict | Evidence |
|---|---|---|
| **buildingSMART IDS 1.0** via `ifctester` (IfcOpenShell) | **Adopt for property rules** | Installs clean (0.8.5). Real open standard since June 2024, with BCF issue output. |
| **depthmapX** (UCL) | Don't wrap | C++ GUI/CLI for *urban* axial and segment analysis on line maps. We already have a room graph, so its whole front half is irrelevant. |
| Space-syntax pip package | **Does not exist** | `spacesyntax`, `space-syntax`, `depthmapx`, `pysyntax`, `jpg-syntax` all fail to resolve. The QGIS Space Syntax Toolkit is a GUI front-end to depthmapX, not a library. |
| **ResPlan `plan_to_graph()`** | Already used | Gives a NetworkX room graph; we use it as an adjacency reference (precision 0.9944 against ours). |
| **Solibri** | Borrow the *architecture*, not the product | Commercial. But its shape is the right one: **50+ rule templates that users parameterise**, not hardcoded rules. |
| GPLAN, HouseGAN++, HouseDiffusion | Reference only | Research code, RPLAN-shaped, not a rule library. |

### Why IDS cannot carry our rules
Its six facets are **Entity, Attribute, Classification, Property, Material,
PartOf** — information requirements. It expresses *"every IfcSpace has a Name and
a NetFloorArea between 7.5 and 30 m²"* very well. It has **no facet for
adjacency, reachability or depth**, so it cannot express *"a bathroom must not be
reachable only through a bedroom"*. Verified by inspecting the installed module,
not inferred from documentation.

**Conclusion:** adopt IDS for the declarative property layer if/when we emit IFC;
build the topological layer ourselves. At room-graph scale the metrics are ~40
lines over NetworkX-style BFS, so building beats wrapping a C++ GUI.

---

## 2. Space Syntax — the formalism for circulation

Hillier & Hanson's justified plan graph. Rooms are nodes, doors are edges, the
graph is rooted at the entrance. Formulas as used in arXiv 2602.22507:

```
TD_i  = Σ shortest-path step depth from i to every other node
MD_i  = TD_i / (k - 1)                                    mean depth
RA_i  = 2 (MD_i - 1) / (k - 2)                            relative asymmetry
D_k   = 2 { k [ log2((k+2)/3) - 1 ] + 1 } / ((k-1)(k-2))  diamond value
RRA_i = RA_i / D_k                                        real relative asymmetry
Int_i = 1 / RRA_i                                         integration
```

Plus **connectivity** (degree) and **control value** (Σ 1/deg over neighbours — a
gatekeeper scores high).

### Why this is the right frame
Integration measures how much a space is a *configurational core*. In a real
house the living room has the highest integration and bedrooms the lowest, and
**that gradient IS the privacy structure**. So:

- a bedroom with above-average integration is *functioning as a corridor*
- a plan where the most integrated room is not the living room is *organised
  around the wrong space*
- if private and public integration are similar, there is *no privacy gradient*

Those are the same three faults I had hand-rolled as ad-hoc depth rules. The
syntax formulation is measurable, comparable across plan sizes, and has published
baselines.

Plan-level metrics, from the same paper:

```
public_score    = max(living integration) - max(non-living integration)
living_relative = max(living integration) / mean(integration)
privacy_gradient = mean(private integration) / mean(public integration)
```

The paper's finding, which is directly our problem: generated layouts capture the
broad hierarchy but **under-express the living room's dominance** relative to
real plans.

Implemented in `src/fpeval/syntax.py` with four checks:
`SYNTAX.LIVING_NOT_CORE`, `SYNTAX.WEAK_HIERARCHY`,
`SYNTAX.NO_PRIVACY_GRADIENT`, `SYNTAX.PRIVATE_ROOM_INTEGRATED`.

---

## 3. Zoning — public / private / service

Standard space-planning practice: group spaces into functional zones, separate
conflicting ones, connect with circulation.

| Zone | Rooms | Wants |
|---|---|---|
| Public | living, dining, foyer, sitout | shallow, high integration, near entrance |
| Private | bedroom, master_bedroom, study | deep, low integration, clustered |
| Service | kitchen, utility, store, bathroom, shaft | near what they serve; wet stacked |
| Circulation | foyer, passage, stair | high control value, is the spine |

A zoning failure is measurable: private rooms interleaved with public ones, or a
zone whose members are not contiguous.

---

## 4. Layout optimisation — the adjacency *preference matrix*

The literature (EvoArch, physics-inspired SLP, RL approaches) converges on a
**weighted adjacency preference matrix**, not a list of required pairs, optimised
multi-objectively with a Pareto front.

Our solver currently takes `required_adjacency` as **binary pairs at a flat 2500
penalty**. A weighted matrix is the correct upgrade: `kitchen↔dining` at 1.0,
`living↔balcony` at 0.7, `pooja↔kitchen` at −0.4 (mildly discouraged),
`kitchen↔bathroom` at −∞ (forbidden). One data structure covers required,
preferred, discouraged and forbidden.

---

## 5. Rule library architecture — Solibri's shape

Not 40 hardcoded functions. **Templates + parameters**, so a rule is data:

```
template: min_clear_width
  applies_to: [bedroom, living, dining, study]
  param: 2400 mm            # NBC 2016 Part 3
  severity: error
  profile: NBC_2016         # relaxed profile overrides param to 2100
```

This is what makes a rule set portable to another city, and what lets a user
disagree with one judgement without losing the arithmetic. `policy.RuleConfig`
already gives per-family and per-rule switches with severity override; turning
the checks themselves into parameterised templates is the next step.

---

## 6. Our rule taxonomy as it stands

| Family | n | Kind of statement | Default |
|---|---|---|---|
| `GEO` | 12 | arithmetic — overlap, degeneracy, reachability | always on |
| `NBC` | 15 | law — minima, ceiling, passage width | always on |
| `BYLAW` | 6 | law — setback, coverage, FAR, RWH | on when the site is surveyed |
| `VASTU` | 9 | client preference, weighted | toggle: off / advisory / strict |
| `DESIGN` | 19 | domain judgement — zoning, faults, work triangle | on, switchable |
| `TYPO` | 4 | typology-conditional adjacency | on, switchable |
| `SYNTAX` | 4 | configurational — integration, privacy gradient | on, switchable |

**69 checks.** GEO and NBC are not opinions and should never be switched off.
Everything below VASTU is judgement and must be.

---

## Sources

Space syntax: [Space Syntax-guided Post-training](https://arxiv.org/html/2602.22507v1) ·
[justified plan graph theory](https://www.researchgate.net/publication/226726014_The_Mathematics_of_Spatial_Configuration_Revisiting_Revising_and_Critiquing_Justified_Plan_Graph_Theory) ·
[spatial form analysis](https://www.spacesyntax.online/applying-space-syntax/building-methods/spatial-form-analysis/) ·
[visual privacy case study](https://link.springer.com/article/10.1007/s00004-025-00819-x)

Layout optimisation: [physics-inspired SLP](https://arxiv.org/pdf/2406.14840) ·
[EvoArch](https://www.sciencedirect.com/science/article/abs/pii/S0010448509001109) ·
[RL for layout](https://academic.oup.com/jcde/article/11/3/43/7636504) ·
[adjacency matrix extraction](https://www.sciencedirect.com/science/article/pii/S2095263523000924)

Rule checking: [buildingSMART IDS](https://technical.buildingsmart.org/projects/information-delivery-specification-ids/) ·
[IDS repo](https://github.com/buildingSMART/IDS) ·
[Solibri rule-based checking](https://bimcorner.com/a-few-words-about-rule-based-model-checking/) ·
[Solibri checking model](https://help.solibri.com/hc/en-us/articles/1500005009042-Understanding-Checking)

Zoning and faults: [zoning diagrams](https://learnarchitecture.net/architectural-diagrams/33891-zoning-diagram-guide.html) ·
[spatial planning](https://www.architecturecourses.org/design/spatial-planning-and-design) ·
[common plan faults](https://www.redesigndaily.com/home-design/living-room/layout/architects-advice-7-common-floor-plan-mistakes-and-how-to-fix-them-44600401)

Tools: [depthmapX](https://spacegroupucl.github.io/depthmapX/) ·
[QGIS Space Syntax Toolkit](https://plugins.qgis.org/plugins/esstoolkit/)
