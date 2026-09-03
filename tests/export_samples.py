"""Export converted OpenPlan3D Projects for the TypeScript verifier.

Prefers the persisted eval corpus (out/eval_corpus/projects.json, written by
`run_corpus.py --manifest`) so the JS run and the Python run score the exact
same artefacts. Falls back to converting on the fly.

  uv run --with shapely --with numpy python tests/export_samples.py [N]
"""
from __future__ import annotations
import json
import os
import pickle
import sys
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
warnings.simplefilter("error", RuntimeWarning)

from fpeval.resplan import convert          # noqa: E402
from fpeval.project import to_project       # noqa: E402
from fpeval import metrics as M             # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
DEST = os.path.join(REPO, "tests", "js", "samples.json")
CORPUS = os.path.join(REPO, "out", "eval_corpus", "projects.json")


def expect_block(ir) -> dict:
    rfm = M.room_face_match(ir)
    return {
        "n_rooms": len(ir.rooms),
        "n_walls": len(ir.walls),
        "n_openings": len(ir.openings),
        "areas_m2": sorted(round(r.area / 1e6, 2) for r in ir.rooms),
        "room_areas_by_id": {r.id: round(r.area / 1e6, 2) for r in ir.rooms},
        "n_faces": rfm.get("n_faces"),
        "extra_faces": rfm.get("extra_faces"),
        "all_rooms_matched": rfm.get("all_matched"),
    }


def main() -> None:
    if os.path.exists(CORPUS) and "--fresh" not in sys.argv:
        with open(CORPUS) as f:
            out = json.load(f)
        out = out[:N]
        src = os.path.relpath(CORPUS, REPO)
    else:
        pkl = M.default_pkl_path()
        plans = pickle.load(open(pkl, "rb"))
        out = []
        for raw in plans:
            if len(out) >= N:
                break
            try:
                ir = convert(raw)
                p = to_project(ir)
                p["_expect"] = expect_block(ir)
                out.append(p)
            except Exception:
                continue
        src = os.path.relpath(pkl, REPO)

    with open(DEST, "w") as f:
        json.dump(out, f)
    size = os.path.getsize(DEST) / 2 ** 20
    print(f"exported {len(out)} projects from {src} -> tests/js/samples.json ({size:.1f} MiB)")


if __name__ == "__main__":
    main()
