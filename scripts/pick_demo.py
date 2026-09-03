import pickle, sys, json, warnings
warnings.filterwarnings("ignore")
import _bootstrap  # noqa: F401  (path + cwd)
from fpeval.resplan import convert
from fpeval.project import to_project
plans = pickle.load(open("/tmp/resplan/data/ResPlan.pkl","rb"))
picked = []
for raw in plans[:400]:
    try:
        ir = convert(raw)
    except Exception: continue
    nb = sum(1 for r in ir.rooms if r.category=="bedroom")
    if 7 <= len(ir.rooms) <= 10 and nb >= 2 and len(ir.openings) >= 8:
        p = to_project(ir); p["id"] = f"demo-{ir.id}"; p["name"] = f"ResPlan {ir.id}"
        picked.append((p, len(ir.walls), len(ir.rooms), len(ir.openings)))
    if len(picked) == 3: break
json.dump([p for p,_,_,_ in picked], open("out/demo_projects.json","w"))
for p,w,r,o in picked: print(f"{p['id']}: {w} walls, {r} rooms, {o} openings")
