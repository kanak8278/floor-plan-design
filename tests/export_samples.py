import pickle, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from fpeval.resplan import convert
from fpeval.project import to_project
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
plans = pickle.load(open("/tmp/resplan/data/ResPlan.pkl", "rb"))
out = []
for raw in plans[:N]:
    try:
        ir = convert(raw)
        p = to_project(ir)
        p["_expect"] = {"n_rooms": len(ir.rooms), "areas_m2": sorted(round(r.area/1e6,2) for r in ir.rooms)}
        out.append(p)
    except Exception:
        pass
json.dump(out, open("tests/js/samples.json","w"))
print(f"exported {len(out)} projects")
