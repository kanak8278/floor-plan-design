"""Export converted ResPlan plans + a loader page so a human can browse them
in the real editor. The editor reads projects from localStorage, which is
per-browser-profile, so a seeding page is the only way to hand them over."""
import pickle, json, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from fpeval.resplan import convert
from fpeval.project import to_project

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
plans = pickle.load(open("data/ResPlan.pkl", "rb"))
out, seen = [], set()
for raw in plans:
    if len(out) >= N: break
    try: ir = convert(raw)
    except Exception: continue
    nb = sum(1 for r in ir.rooms if r.category == "bedroom")
    key = (len(ir.rooms), nb)
    if key in seen or not (5 <= len(ir.rooms) <= 12): continue
    seen.add(key)
    p = to_project(ir)
    p["id"] = f"fp-{ir.id}"
    p["name"] = f"ResPlan {ir.id} — {nb}BHK, {len(ir.rooms)} rooms"
    p["_meta"] = {"rooms": len(ir.rooms), "bedrooms": nb, "walls": len(ir.walls),
                  "openings": len(ir.openings),
                  "area_m2": round(sum(r.area for r in ir.rooms)/1e6, 1)}
    out.append(p)
json.dump(out, open("vendor/openPlan3D/static/fpeval/projects.json", "w"))
print(f"wrote {len(out)} projects")
for p in out:
    m = p["_meta"]
    print(f"  {p['id']:<12} {m['bedrooms']}BHK  {m['rooms']} rooms  {m['walls']} walls  {m['area_m2']} m²")
