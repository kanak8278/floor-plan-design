"""End-to-end: envelope -> CP-SAT -> validator -> renderer -> Project JSON.

Each module was built in isolation by a different agent, so this is the first
test that runs the whole chain. Interface drift and unit mismatches show up here
and nowhere else.
"""
import json, sys, time, xml.etree.ElementTree as ET

from fpeval.bylaws import BENGALURU
from fpeval.envelope import compute_envelope, CityProfileAdapter, RoomReq
from fpeval.solver import solve_layout, LayoutSpec
from fpeval.rules import validate
from fpeval.render import render
from fpeval.project import to_project, from_project
from fpeval.metrics import face_recovery, ir_identity

PROF = CityProfileAdapter(BENGALURU)


def programme(bhk: int) -> list[RoomReq]:
    """A realistic Indian programme: BHK bedrooms + hall + kitchen + baths."""
    rs = [RoomReq(id="living", name="Living", category="living", target_m2=18.0,
                  weight=1.0, vastu_zone="N", is_entrance=True),
          RoomReq(id="kitchen", name="Kitchen", category="kitchen", target_m2=8.0,
                  weight=1.0, vastu_zone="SE")]
    for i in range(bhk):
        rs.append(RoomReq(id=f"bed{i+1}", name=f"Bedroom {i+1}", category="bedroom",
                          target_m2=13.0 if i == 0 else 10.5, weight=1.0,
                          vastu_zone="SW" if i == 0 else None))
    for i in range(max(1, bhk - 1)):
        rs.append(RoomReq(id=f"bath{i+1}", name=f"Bathroom {i+1}", category="bathroom",
                          target_m2=3.6, weight=0.8, vastu_zone="NW"))
    return rs


CASES = [
    ("30x40 2BHK N-facing", 30, 40, "N", 2, True),
    ("30x40 3BHK E-facing", 30, 40, "E", 3, True),
    ("40x60 3BHK E-facing", 40, 60, "E", 3, True),
    ("20x30 3BHK",          20, 30, "N", 3, False),   # must be refused, not faked
]

print(f"{'case':<24}{'status':<12}{'t_s':>6}{'rooms':>7}{'IoU':>9}"
      f"{'err':>5}{'warn':>6}{'vastu':>7}{'svg_kb':>8}")
print("-" * 84)
fails = []
for label, w, d, facing, bhk, expect_ok in CASES:
    prog = programme(bhk)
    t0 = time.time()
    stmt = compute_envelope(w, d, road_facing=facing, profile=PROF, programme=prog)
    res = solve_layout(w, d, LayoutSpec(programme=prog, entrance_room="living",
                                        time_limit_s=12.0),
                       road_facing=facing, profile=PROF, plan_id=f"itg-{w}x{d}-{bhk}")
    el = time.time() - t0
    status = str(getattr(res, "status", "?"))
    plan = getattr(res, "plan", None)

    if plan is None:
        print(f"{label:<24}{status:<12}{el:6.1f}{'-':>7}{'-':>9}{'-':>5}{'-':>6}{'-':>7}{'-':>8}")
        if expect_ok: fails.append(f"{label}: expected a plan, got {status}")
        else:
            groups = getattr(res, "infeasible_groups", None)
            print(f"{'':24}  correctly refused; groups={groups}")
        continue

    findings = validate(plan, brief=None, profile=BENGALURU)
    errs = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    vastu = [f for f in findings if f.rule_id.startswith("VASTU")]

    fr = face_recovery(plan)
    proj = to_project(plan); blob = json.dumps(proj)
    ident = ir_identity(plan, from_project(proj))
    svg = render(plan, "presentation")
    svg_ann = render(plan, "annotated", findings=findings)
    ET.fromstring(svg); ET.fromstring(svg_ann)          # must be well-formed

    print(f"{label:<24}{status:<12}{el:6.1f}{len(plan.rooms):>7}"
          f"{fr['area_iou']:>9.4f}{len(errs):>5}{len(warns):>6}"
          f"{len(vastu):>7}{len(svg)/1024:>8.1f}")

    if not expect_ok: fails.append(f"{label}: expected refusal, got a plan")
    if fr["area_iou"] < 0.99: fails.append(f"{label}: face IoU {fr['area_iou']:.4f}")
    if errs: fails.append(f"{label}: {len(errs)} validator errors: "
                          + ", ".join(f.rule_id for f in errs[:4]))
    if not (ident["walls_equal"] and ident["openings_equal"] and ident["rooms_equal"]):
        fails.append(f"{label}: Project round-trip not identity")

print("-" * 84)
if fails:
    print(f"FAIL ({len(fails)}):")
    for f in fails: print("  -", f)
    sys.exit(1)
print("PASS — envelope -> solver -> validator -> renderer -> Project all agree")
