import json, sys
import _bootstrap  # noqa: F401  (path + cwd)
from fpeval.imgcorpus import verify
d = json.load(open("out/extract_probe.json"))
v = verify(d)
print(f"plot: {d.get('plot')}")
print(f"ACCEPTED: {v.accepted}")
for r in v.reasons: print(f"  REASON: {r}")
print(f"stats: {v.stats}\n")
print(f"{'room':<14}{'status':<11}{'mm':<14}{'from feet':<16}{'err'}")
for c in v.rooms:
    ft = f"{tuple(round(x) for x in c.ft_mm)}" if c.ft_mm else "-"
    err = f"{c.worst_err_mm:.0f}mm ({100*c.worst_err_pct:.1f}%)" if c.worst_err_mm is not None else ""
    print(f"{c.name[:13]:<14}{c.status:<11}{str(c.mm or '-'):<14}{ft:<16}{err}")
