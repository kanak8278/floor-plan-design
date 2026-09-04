"""Classifier gate accuracy on a labelled set with deliberate hard negatives.
Lives in `scripts/`, not `tests/`: it reads `corpus/india/raw/*`, which is
not part of the repo's committed fixtures, and it reports accuracy rather than
asserting a bound.
"""
import json, sys, glob, os
import _bootstrap  # noqa: F401
from fpeval.imgclass import classify

# ground truth from my own inspection of each file
EXPECT = {
    "happho-30x40-duplex-gf.jpg": ("floor_plan_2d", True),
    "ff-plan.jpg":                ("floor_plan_2d", True),
    "neg-elevation.png":          ("elevation", False),
    "neg-3drender.jpg":           ("3d_render_exterior", False),
    "neg-photo.jpg":              ("photograph", False),
    "neg-detail.jpg":             ("detail_drawing", False),
    "edge-3dplan.jpg":            ("floor_plan_3d_isometric", False),
}
rows, kind_ok, usable_ok = [], 0, 0
for f in sorted(glob.glob("corpus/india/raw/*")):
    base = os.path.basename(f)
    if base not in EXPECT: continue
    exp_kind, exp_usable = EXPECT[base]
    c = classify(f)
    k = c.kind == exp_kind
    u = c.usable == exp_usable
    kind_ok += k; usable_ok += u
    rows.append(c.as_dict())
    aff = "".join(x for x, on in [("L", c.raw["has_room_labels"]), ("D", c.raw["has_printed_room_dimensions"]),
                                  ("2", c.raw["has_dual_units"]), ("P", c.raw["has_plot_dimensions"]),
                                  ("N", c.raw["has_north_arrow"]), ("S", c.raw["has_scale_bar_or_ratio"]),
                                  ("I", c.raw["appears_indian"])] if on)
    print(f"{base:<32}{c.kind:<26}usable={str(c.usable):<6}{'kind OK' if k else 'KIND WRONG->'+exp_kind:<14} "
          f"{'' if u else 'USABLE WRONG'} conf={c.confidence:.2f} aff=[{aff}]")
    if c.raw["blocking_problems"]:
        print(f"{'':32}blocking: {'; '.join(c.raw['blocking_problems'])[:110]}")
n = len(rows)
print(f"\nkind correct  : {kind_ok}/{n}")
print(f"usable correct: {usable_ok}/{n}")
print("legend: L=room labels D=printed dims 2=dual units P=plot dims N=north arrow S=scale I=looks Indian")
json.dump(rows, open("out/imgclass_report.json","w"), indent=1)
