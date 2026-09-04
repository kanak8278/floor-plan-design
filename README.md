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

```bash
# tests (offline only)
uv run --with pytest --with shapely --with numpy --with ortools \
       python -m pytest -m "not api and not slow"

# the prompt suite: 50 general + 50 detailed
uv run --with shapely --with numpy --with ortools \
       python scripts/run_suite.py --track A --set paired

# named plans for the browser, then open http://localhost:5199/fpeval
uv run --with shapely --with numpy --with ortools \
       python scripts/build_suite_gallery.py --set paired --svg

# the design service
uv run --with fastapi --with uvicorn --with shapely --with numpy --with ortools \
       uvicorn service.app:app --port 8099

# the editor (serves /fpeval and proxies /api/* to the service)
cd vendor/openPlan3D && npx vite dev --port 5199
```

## Documents

| File | What it is |
|---|---|
| `PLAN.md` | architecture, agent tool surface, sequenced next steps |
| `DECISIONS.md` | the load-bearing choices and why |
| `DOMAIN.md` | domain survey: what to borrow, what to build, space syntax |
| `FEATURES_AUDIT.md` | OpenPlan3D inventory, read from source |
| `PROGRESS.md` | chronological log, one line per verified step |

## Two rules the code is built around

1. **The LLM emits specifications and symbolic patches; solvers emit
   coordinates.** Enforced in `llm.py`, not merely requested.
2. **Integer millimetres are authoritative.** OpenPlan3D's `Project` is float
   centimetres and therefore a lossy projection of the IR, never the source.
