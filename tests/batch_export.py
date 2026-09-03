import pickle, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from fpeval.resplan import convert
from fpeval.project import to_project
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
plans = pickle.load(open("data/ResPlan.pkl","rb"))
out = []
for raw in plans[:N*2]:
    try:
        ir = convert(raw)
    except Exception: continue
    p = to_project(ir); p["id"] = f"b-{ir.id}"
    p["_expect"] = {"rooms": len(ir.rooms), "walls": len(ir.walls),
                    "doors": sum(1 for o in ir.openings if o.kind != "window"),
                    "windows": sum(1 for o in ir.openings if o.kind == "window"),
                    "area_m2": round(sum(r.area for r in ir.rooms)/1e6, 1)}
    out.append(p)
    if len(out) == N: break
json.dump(out, open("out/batch_projects.json","w"))
print(f"exported {len(out)} projects for browser batch test")
