# fpeval — LLM-steered Indian floor-plan generation

Concept/sales-grade plans for Indian plots and apartment units, steered by
prompt, editable by hand, and checked by a rules engine rather than by eye.

## Layout

```
src/fpeval/        the library — 29 modules, no scripts, no test code
tests/             pytest tests only (conftest.py handles imports and cwd)
tests/js/          TypeScript conformance tests against the vendored fork
scripts/           runnable CLI tools: suite runs, corpus ingest, galleries
scripts/browser/   Playwright harnesses that drive the real editor
service/           FastAPI design service (stateless; the browser holds the doc)
suite/             the prompt suite — 160 examples with machine-checkable truth
data/              ResPlan (246 MB pkl, gitignored) + its manifest
corpus/india/      Indian builder plan images (gitignored)
vendor/openPlan3D/ the MIT fork we build the editor on
out/               generated artefacts (gitignored)
```

`src/fpeval` holds the library and nothing else. Anything with a `__main__` or a
CLI lives in `scripts/`; anything pytest should collect lives in `tests/`.

## Running things

Every dependency is declared in `pyproject.toml`, so `uv run` needs no flags.
One `uv sync --all-extras` and the commands below work as written; the long
`--with shapely --with numpy --with ortools` prefixes these used to carry were
there only because `ortools` was never declared.

```bash
uv sync --all-extras          # once

# tests (offline only)
uv run pytest -m "not api and not slow"

# the prompt suite: 50 general + 50 detailed
uv run python scripts/run_suite.py --track A --set paired

# named plans for the browser, then open http://localhost:5199/fpeval
uv run python scripts/build_suite_gallery.py --set paired --svg

# the design service
uv run uvicorn service.app:app --port 8099

# the editor (serves /fpeval and proxies /api/* to the service)
cd vendor/openPlan3D && npx vite dev --port 5199
```

## Documents

| File | What it is |
|---|---|
| `PLAN.md` | architecture, agent tool surface, sequenced next steps |
| `DECISIONS.md` | the load-bearing choices and why |
| `DOMAIN.md` | domain survey: what to borrow, what to build, space syntax |
| `SPACES.md` | every space in a home: purpose, position, and the rules that fall out |
| `RULES.md` | the flat checklist: 100 implemented rules and 62 gaps, one line each |
| `COUPLING.md` | audit: which of the 100 rules the solver can actually act on |
| `REVIEW.md` | recall pass: what an eye catches on rendered plans that no rule does |
| `FEATURES_AUDIT.md` | OpenPlan3D inventory, read from source |
| `PROGRESS.md` | chronological log, one line per verified step |

## Two rules the code is built around

1. **The LLM emits specifications and symbolic patches; solvers emit
   coordinates.** Enforced in `llm.py`, not merely requested.
2. **Integer millimetres are authoritative.** OpenPlan3D's `Project` is float
   centimetres and therefore a lossy projection of the IR, never the source.
