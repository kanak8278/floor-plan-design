"""Room aspect ratios measured off the hand-annotated corpus.

Regenerates the table quoted in `standards.MAX_ASPECT`. The point of the
constant is that it is measured, so the measurement has to be re-runnable.
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

rows: list[tuple[str, float, float]] = []
for f in sorted((ROOT / "corpus/india/truth").glob("*.json")):
    for r in json.loads(f.read_text())["rooms"]:
        if r.get("category") and r.get("w_mm") and r.get("d_mm"):
            rows.append((r["category"], float(r["w_mm"]), float(r["d_mm"])))

if not rows:
    print("no annotated rooms with dimensions found"); raise SystemExit(1)

d: dict[str, list[float]] = defaultdict(list)
for c, a, b in rows:
    d[c].append(max(a, b) / min(a, b))

print(f"{len(rows)} rooms from {len(list((ROOT/'corpus/india/truth').glob('*.json')))} sheets\n")
print(f"{'category':<16}{'n':>3}{'min':>7}{'median':>8}{'p90':>7}{'max':>7}")
for c in sorted(d, key=lambda k: -len(d[k])):
    v = sorted(d[c]); n = len(v)
    print(f"{c:<16}{n:>3}{v[0]:>7.2f}{v[n//2]:>8.2f}"
          f"{v[min(n-1, int(0.9 * n))]:>7.2f}{v[-1]:>7.2f}")
