"""Do our generated plans show the spatial hierarchy real plans do?

The reference finding (arXiv 2602.22507) is that generated layouts capture the
broad hierarchy but under-express the living room's dominance. This measures our
own output against real ResPlan plans on the same metrics, so the defect has a
number and a target instead of an opinion.
"""
import pickle, sys, statistics as st, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from fpeval.resplan import convert
from fpeval.syntax import analyse, check
from fpeval.suite import load_suite
from fpeval.score import run
from fpeval.rules import _build_ctx, _adj
from fpeval.bylaws import BENGALURU


def syntax_of(plan):
    ctx = _build_ctx(plan, {}, BENGALURU)
    adj = _adj(ctx)
    meta = {r.id: (r.name, r.category) for r in plan.rooms}
    ent = (ctx.entry_rooms or [None])[0]
    return analyse(adj, meta, ent)


def summarise(label, plans):
    rows = []
    for pl in plans:
        s = syntax_of(pl)
        if s.k < 3 or not s.nodes:
            continue
        liv_is_core = s.nodes[s.core].category == "living"
        rows.append((s.public_score, s.living_relative, s.privacy_gradient,
                     liv_is_core, len(check(s))))
    if not rows:
        print(f"{label}: nothing measurable"); return None
    ps = [r[0] for r in rows]; lr = [r[1] for r in rows if r[1]]
    pg = [r[2] for r in rows if r[2]]; core = [r[3] for r in rows]
    nf = [r[4] for r in rows]
    print(f"{label:<26} n={len(rows):<5} "
          f"living_is_core={100*st.mean(core):5.1f}%  "
          f"public_score med={st.median(ps):+7.3f}  "
          f"living_rel med={st.median(lr) if lr else float('nan'):5.2f}  "
          f"privacy med={st.median(pg) if pg else float('nan'):5.2f}  "
          f"findings/plan={st.mean(nf):.2f}")
    return rows


print("=== real ResPlan plans ===")
raw = pickle.load(open("data/ResPlan.pkl", "rb"))
real = []
for r in raw[:400]:
    try: real.append(convert(r))
    except Exception: pass
summarise("ResPlan (real)", real)

print("\n=== our generated plans ===")
gen = []
for e in load_suite():
    if "A" not in e.tracks: continue
    res = run(e, track="A", time_limit_s=8)
    if res.plan is not None: gen.append(res.plan)
summarise("fpeval (generated)", gen)
