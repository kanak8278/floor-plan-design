# Plan and design

Canonical design document. `PROGRESS.md` is the chronological log, `DECISIONS.md`
the load-bearing choices, `FEATURES_AUDIT.md` the OpenPlan3D inventory. This file
is where the system is going and why.

---

## 1. Where we are

Target: **concept / sales-grade** floor plans, **India (BBMP first)**, **plot-driven**,
steered by prompt, editable by hand.

Built and measured:

| Component | State |
|---|---|
| Canonical IR (integer mm, wall-centreline graph, parametric openings, derived room faces, Stair, Furniture) | done |
| `roomtypes.py` — 18-type canonical taxonomy | done |
| ResPlan → IR converter | 17,000/17,000 clean, 100% identity |
| IR ↔ OpenPlan3D `Project` adapter | 400/400 identity; 99.9% polygon recovery on read-back |
| `bylaws.py` — BBMP bands, NBC minima, Vastu table (all data, not code) | done |
| `rules.py` — 33 GEO/NBC/BYLAW rules + 9 weighted Vastu | 47 tests, ~2.4 ms |
| `envelope.py` — plot → setbacks → coverage → FAR → area budget, audited | done |
| `solver.py` — slicing tree + CP-SAT | face IoU 1.000000 on every success, p50 2.18 s |
| `render.py` — headless SVG, presentation + annotated | 1000 renders, 0 failures, deterministic |
| `spec.py` / `llm.py` / `brief.py` | 100% schema conformance, 96.4% field-correct |
| India image corpus pipeline (classify → extract → 3 checksums) | done |
| `suite/` — 110 examples with machine-checkable ground truth | validates; **not yet run** |
| `service/app.py` + same-origin proxy | generate / validate / render / roomtypes |
| Furnishing system | in flight |

Not built: the agent tool surface, the chat UI, the closed repair loop, the suite
scorer, sections/elevations, real DWG.

---

## 2. Architecture

```
┌─ Browser: forked OpenPlan3D (one document, one undo stack) ─────────────┐
│  FloorPlanCanvas 2D  │  ThreeViewer 3D  │  Chat panel  │  Findings badges│
│         ▲                                      │                         │
│         │ project.ts mutation API               │ tool calls              │
│         │ (beginUndoGroup / endUndoGroup)       ▼                         │
│  ┌──────┴───────────────────────────────────────────────────────────┐    │
│  │  Project JSON = a PROJECTION of the document, for rendering and  │    │
│  │  optimistic local edits. Authority lives in the service.          │    │
│  └──────┬───────────────────────────────────────────────────────────┘    │
└─────────┼────────────────────────────────────────────────────────────────┘
          │ same-origin /api/*   (SvelteKit proxy, no CORS, one deploy)
┌─────────▼────────────────────────────────────────────────────────────────┐
│  Python service — pure compute is STATELESS (generate / validate /      │
│  render). The DOCUMENT is server-owned: one command log, one applier.    │
│                                                                          │
│   spec extraction (LLM) ──► DesignSpec ──► envelope ──► CP-SAT solver    │
│                                                              │           │
│                                                        canonical IR      │
│                                                          │      ▲        │
│                                                          ▼      │        │
│                        rules engine ── findings ──► LLM patch proposal   │
│                                                          │               │
│                        furnishing solver ◄───────────────┘               │
│                        renderer (SVG)                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

Three invariants, each already load-bearing:

1. **The LLM emits specs and symbolic patches; solvers emit coordinates.** Numeric
   constraint satisfaction is what models are worst at and exactly what makes a plan
   buildable. `llm.py` rejects coordinate emission in code, not by instruction.
2. **The document lives in the browser.** The service is stateless, so there is no
   server/client desync, and the editor's undo stack remains the only history.
3. **Wall-network-first IR.** Rooms are faces, openings are parametric. This is what
   lets a prompt-edit and a mouse-drag be the same operation on one substrate.

---

### Where the document lives, and why that changed

The service was designed stateless, with the browser owning the document and
posting it in with every call. Pure compute still works that way and should:
`generate`, `validate` and `render` are functions of their input, and keeping
them stateless is what makes them cheap to scale and trivial to deploy.

The **document** moved to the service, for three reasons that only appeared
once a second actor started editing:

1. **The browser does not own durable storage.** `localStorage` is
   per-browser, quota-limited, and cleared with site data. `datastore.ts`
   already has a `QuotaExceededError` branch that deletes the user's *other*
   projects to save the current one — not a storage layer that should hold the
   only copy of a design.
2. **A human plus an agent need one serialisation point.** Without it you get
   the failure this actually produced twice: the plan reading "Room 1" while
   the chat insisted the room was the master bedroom.
3. **Documents are not small.** Background images and custom entourage are
   base64 data URLs *inside* the Project, so posting the whole thing per call
   is unbounded on a traced site plan.

"Stateless" in production normally means stateless *processes* with state in a
database, which is what this is: `service/store.py` behind a `DocumentStore`
interface, SQLite by default. The client still applies every edit locally so
the canvas responds at pointer speed — but that is a *prediction*, reconciled
against the service by state hash, and the cost of it being wrong is one
repaint rather than a corrupted document.

See `DECISIONS.md` #7-#9 for the substrate: one applier, persisted room
identity, and the IR as a superset of the editor's model.

## 3. The tool surface — few tools, deep parameters

**Do not expose one tool per action.** Breadth comes from parameters and queries, not
tool count. Five tools, fixed.

Three reasons this is not just taste:

- **Measured API limits.** Strict tool use caps unions at 16 and optional params at 24,
  and the `DesignSpec` schema already sits *at* the compiled-grammar size cap — nesting
  it one level deeper returns a 400. More tools would break the request outright.
- **Tool definitions are re-billed every call** unless the `tools` block is cached. A
  7 KB schema on every turn is real money; twenty schemas is worse.
- A small surface with a discriminated union of ops is more reliable than a wide surface,
  because the model picks a *tool* once and then reasons inside a typed payload.

### 3.1 `discover` — the capability meta-tool

One tool answers "what can this system do", so the prompt never carries a stale list.

```
discover(kind, query?, filters?) -> results[]
  kind: "catalog" | "room_types" | "capabilities" | "rules" | "style_packs"
                 | "city_profiles" | "examples"
```

- `catalog` — 189 furniture + 12 entourage, filterable by category and dimension.
  Returns real `catalogId`s, so a selection is always renderable.
- `room_types` — the 18 canonical types with NBC minima, carpet/built-up flags,
  Vastu zones. Replaces every hard-coded room list.
- `capabilities` — door types, window types, stair types, materials, export formats,
  read from the fork at runtime. **This is how the agent explores features itself.**
- `rules` — the rule catalogue with ids and thresholds, so a patch can cite what it repairs.
- `city_profiles` / `style_packs` / `examples` — the data tables.

### 3.2 `inspect` — the read meta-tool

```
inspect(what, id?) -> digest
  what: "document" | "selection" | "findings" | "area_statement" | "room" | "wall"
```

Returns a **digest, never raw JSON**. A 40-wall plan as raw `Project` is ~13 KB of
coordinates the model cannot use and will hallucinate over. The digest is symbolic:
room names, areas in sq ft and m², adjacency, which walls are shared, which openings
sit on which wall, what the user has selected, what is pinned.

### 3.3 `apply` — the single mutation tool

```
apply(ops[], description) -> {applied, rejected, findings}
```

`ops` is a **discriminated union of the 19 existing `PatchOp` types** — 11 spec-level,
8 geometry-level. One tool, not nineteen. Guarantees:

- Wrapped in one `beginUndoGroup()` / `endUndoGroup(description)`, so an AI edit is a
  single Ctrl-Z for the user.
- Valid ops apply, invalid ones are rejected individually and reported back — partial
  application is a feature, not a failure.
- `description` is mandatory and homeowner-readable. The user must see *"widened the
  hallway to 900 mm"*, not a JSON diff.
- Spec-level ops are preferred and trigger a re-solve; geometry-level ops must cite the
  finding they repair.

### 3.4 `solve` — re-run the engine

```
solve(spec_patch?, pinned?) -> {status, document, findings, area_statement}
```

Returns `INFEASIBLE` with the binding constraint groups as a **legitimate answer**.
`pinned` carries the user's manually-touched elements so a re-solve cannot stomp them.

### 3.5 `look` — the visual channel, narrow on purpose

```
look(mode) -> image
  mode: "presentation" | "annotated"
```

For presentation problems only — label collisions, awkward proportions, "does this read
as a home". **Never for geometry.** A vision model cannot tell 820 mm from 900 mm; the
rules engine can, exactly, in 3 ms. The annotated render's own title block says so.

---

## 4. The live-render loop

Rendering is **client-side**, not a server push, so the canvas updates at interaction
speed and the server stays stateless.

```
user types  →  POST /api/agent  (document digest + prompt + selection)
            →  agent loop runs server-side (discover / inspect / solve)
            →  streams ops back over SSE
browser     →  applies each op through project.ts as it arrives
            →  Svelte reactivity re-renders 2D and 3D immediately
            →  POST /api/validate  →  findings render as badges on the canvas
```

Streaming matters: applying ops as they arrive makes the plan visibly move while the
model is still thinking. Request/response with a spinner is what makes an integrated
system feel like two bolted together.

Cohesion requirements, all cheap and all easy to skip by accident:

1. **Selection is shared context.** Select a wall, say "make this wider" — the agent
   reads `selectedElementId` from the same store the canvas uses.
2. **Findings are inline canvas badges**, clickable to ask for a fix — so chat is one
   surface among several, not the only door to the AI.
3. **An edit journal.** Every user mutation appends a semantic record, replayed to the
   agent as *"the user widened the hallway to 1.2 m"*. Without this the next turn stomps
   the manual fix — the single most common failure of AI-plus-manual-editing tools.
4. **Pinning.** A user edit marks elements locked; they become fixed variables in CP-SAT.

---

## 5. Authority model

| Tier | Actions | Approval |
|---|---|---|
| Read | `discover`, `inspect`, `look`, `validate` | none |
| Propose | spec-level ops, re-solve | none — visible as a diff, one undo |
| Repair | geometry ops that **cite a validator finding** | none — one undo |
| Confirm | touching pinned elements, deleting, changing many rooms at once | explicit |
| Never | raw coordinates, `localStorage`/file writes, anything not undoable in one step | — |

Principle: **the agent gets exactly the authority the user has in the UI, never more,
and every action is a visible diff reversible in one step.**

---

## 6. Next steps, in order

1. **Suite scorer.** Take an example, run extract_spec → solve → validate, grade each
   ground-truth assertion. This turns the 110 examples into the verifiable-reward loop.
   No new capability needed; highest information per hour.
2. **Close the repair loop.** solve → validate → propose_patch → apply → re-validate,
   bounded at ~5 iterations, non-convergence surfaced rather than looped on. Currently
   `propose_patch` is measured on n=1 with hand-written findings.
3. **Tool surface** (section 3) as `service/agent.py`, plus `POST /api/agent` with SSE.
4. **Edit journal + pinning** in the fork. Pinning already exists in the solver; the
   client half does not.
5. **Chat panel + inline findings badges** in the fork.
6. **Verify the wire conventions** — mm↔cm and the Y-sign against the running editor.
   Currently an assumption, flagged by two agents.
7. **Fix imperial units in the fork.** Their own bug report: room area ignores imperial,
   properties inputs don't convert, status bar always m². India thinks in feet and sq ft.
8. **Renderer gaps**: per-room dimension lines for L-shaped rooms, diagonal-wall chains.
9. **3D cutaway** — `setWallsXray` exists; a real clipping-plane section does not.
10. **Sections and elevations** — needs the vertical model (FFL, floor-to-floor, slab,
    sill/lintel). One data-model change unlocks sections, elevations *and* IFC.
11. **Sanction track** (only if the market pulls): area statement → PreDCR layer naming →
    real DWG via the ODA converter. IFC after that, and only for a BIM customer.

---

## 7. Known gaps, stated plainly

- **Solver: rectangular plots and footprints only.** No L-shapes, no corner plots with
  two frontages, no courtyards. Biggest single limit.
- **Solver: single storey.** The envelope reports storey count; layout does one floor.
  No stair stacking, no wet-stack alignment across floors.
- **No explicit corridors.** Leftover area becomes one "Hall" filler cell.
- **Vastu is weak inside a fixed topology.** Scoring shifts assignment on loose plots but
  barely moves a tight 30×40 — proper Vastu needs zone-aware topology generation.
- **ResPlan is scale-ambiguous (±30%)** and has no north, no setbacks, no circulation
  labels. It tests the plumbing, never plan quality.
- **`temperature` is removed from current models**, so extraction determinism comes from
  two-run consensus, not a seed.
- 4–5 BHK on large plots strands 1–2 rooms with no exterior window (priced, not forbidden).
- `door_width` can over-constrain: 30×50 4BHK reports infeasible on 900 mm doors alone.
- 81 of 248,961 ResPlan openings unhostable; 59 lie outside `inner` entirely.
- Catalogue lacks: Indian squat WC, pooja mandir unit, sump/OHT/septic, chajja, railing.

---

## 8. Open decisions

1. **Which city bye-laws after BBMP?** Determines the second `CityProfile`.
2. **Do we chase the sanction track at all?** It roughly triples scope but is where the
   willingness to pay is. Decide before building sections/elevations.
3. **Indian eval corpus size.** 21 images ingested, 6 accepted. 100–300 verified plans is
   enough for evaluation; sourcing is 1–2 weeks.
4. **Furniture density default** — a sparse plan reads clean, a full one sells. Style-pack
   parameter, but needs a default.
