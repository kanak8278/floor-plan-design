"""Space-syntax metrics on the room graph.

Built rather than borrowed, deliberately. What exists:

* **depthmapX** (UCL, open source) is C++ and does axial/segment analysis on
  *urban line maps*. Wrong scale and wrong input -- we already have a room graph,
  not a line map, so its whole front half is irrelevant.
* **No pip package exists.** `spacesyntax`, `space-syntax`, `depthmapx`,
  `pysyntax` all fail to resolve. The QGIS Space Syntax Toolkit is a GUI
  front-end to depthmapX, not a library.
* **ifctester** (buildingSMART IDS 1.0, part of IfcOpenShell) IS a real
  open-source rule engine with BCF output, and we should adopt it for property
  requirements if we go IFC. But its facets are Entity / Attribute /
  Classification / Property / Material / PartOf -- information requirements.
  There is no facet for "adjacent to", "reachable through" or "depth from
  entrance", so it cannot express the rules that matter here.

At room-graph scale the metrics are ~40 lines over `networkx`, so building is
cheaper than wrapping a C++ GUI.

Formulas (Hillier & Hanson; as used in arXiv 2602.22507):

    TD_i   = sum of shortest-path step depths from i to every other node
    MD_i   = TD_i / (k - 1)                       mean depth
    RA_i   = 2 (MD_i - 1) / (k - 2)               relative asymmetry
    D_k    = 2 { k [ log2((k+2)/3) - 1 ] + 1 } / ((k-1)(k-2))    diamond value
    RRA_i  = RA_i / D_k                           real relative asymmetry
    Int_i  = 1 / RRA_i                            integration

Why this matters for us: integration measures how much a space acts as a
*configurational core*. In a real house the living room has the highest
integration and bedrooms the lowest -- that gradient IS the privacy structure.
The paper's finding is that generated plans capture the broad hierarchy but
under-express the living room's dominance, which is exactly the defect visible
in our own output: a bedroom acting as a corridor is a bedroom with anomalously
high integration.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

INTEGRATION_CAP = 12.0
PUBLIC = ("living", "dining", "foyer", "sitout")
# The *public core* is whichever space the plan is organised around. Grouping
# living with circulation is not a fudge: ResPlan labels the whole hall "living",
# while our solver splits the same function into a `living` room plus a `passage`
# filler cell. Asking "is the core literally the living room" then compares two
# different labelling conventions and reports a failure that is an artefact.
# The question that matters is whether the core is PUBLIC at all.
PUBLIC_CORE = ("living", "dining", "foyer", "passage", "hall")
PRIVATE = ("bedroom", "master_bedroom", "study")


def diamond_value(k: int) -> float:
    """D_k, the normalising constant for a 'diamond-shaped' graph of k nodes."""
    if k < 4:
        return 1.0
    return (2.0 * (k * (math.log2((k + 2) / 3.0) - 1.0) + 1.0)
            / ((k - 1.0) * (k - 2.0)))


@dataclass
class NodeMetrics:
    room_id: str
    name: str
    category: str
    connectivity: int
    total_depth: int
    mean_depth: float
    ra: float
    rra: float
    integration: float
    depth_from_entrance: int | None
    control: float


@dataclass
class PlanSyntax:
    nodes: dict[str, NodeMetrics] = field(default_factory=dict)
    k: int = 0
    # max living integration minus max non-living integration. Positive means the
    # living room really is the core; <= 0 means something else is.
    public_score: float = 0.0
    # living integration / mean integration. Baseline 1.0; real plans sit above.
    living_relative: float = 0.0
    # mean private integration / mean public integration. Should be well under 1.
    privacy_gradient: float = 0.0
    core: str = ""                     # the actual most-integrated room
    notes: list[str] = field(default_factory=list)


def _bfs_depths(adj: dict[str, set[str]], src: str) -> dict[str, int]:
    d = {src: 0}
    frontier = [src]
    while frontier:
        nxt = []
        for u in frontier:
            for v in adj.get(u, ()):
                if v not in d:
                    d[v] = d[u] + 1
                    nxt.append(v)
        frontier = nxt
    return d


def analyse(adj: dict[str, set[str]], meta: dict[str, tuple[str, str]],
            entrance: str | None = None) -> PlanSyntax:
    """`adj` is room_id -> neighbours (door graph). `meta` is id -> (name, category)."""
    ids = [i for i in adj if i in meta]
    k = len(ids)
    out = PlanSyntax(k=k)
    if k < 3:
        out.notes.append(f"only {k} rooms; syntax metrics need >= 3")
        return out

    dk = diamond_value(k)
    ent_depths = _bfs_depths(adj, entrance) if entrance in adj else {}

    for i in ids:
        d = _bfs_depths(adj, i)
        # Unreachable rooms would make TD meaningless; count only the component.
        reach = [v for v in d if v in meta and v != i]
        if not reach:
            continue
        td = sum(d[v] for v in reach)
        kk = len(reach) + 1
        md = td / max(kk - 1, 1)
        ra = 2.0 * (md - 1.0) / max(kk - 2, 1) if kk > 2 else 0.0
        rra = ra / dk if dk else ra
        # A node with MD == 1 (a perfect hub) gives RA = 0 and infinite
        # integration. Mathematically right, useless in an aggregate: an
        # infinity poisons public_score and the privacy ratio. Clamp to a value
        # far above anything real (observed max on ResPlan is ~4).
        integ = (1.0 / rra) if rra > (1.0 / INTEGRATION_CAP) else INTEGRATION_CAP
        # Control value: each node gives 1/deg to each neighbour; a node's control
        # is what it receives. High control = a gatekeeper.
        ctrl = sum(1.0 / max(len(adj.get(n, ())), 1) for n in adj.get(i, ()))
        nm, cat = meta[i]
        out.nodes[i] = NodeMetrics(
            room_id=i, name=nm, category=cat, connectivity=len(adj.get(i, ())),
            total_depth=td, mean_depth=round(md, 4), ra=round(ra, 4),
            rra=round(rra, 4),
            integration=round(min(integ, INTEGRATION_CAP), 4),
            depth_from_entrance=ent_depths.get(i), control=round(ctrl, 3))

    if not out.nodes:
        return out
    ints = {i: n.integration for i, n in out.nodes.items()}
    out.core = max(ints, key=ints.get)
    liv = [v for i, v in ints.items() if out.nodes[i].category in PUBLIC_CORE]
    non_liv = [v for i, v in ints.items() if out.nodes[i].category not in PUBLIC_CORE]
    if liv:
        out.public_score = round(max(liv) - (max(non_liv) if non_liv else 0.0), 4)
        out.living_relative = round(max(liv) / (sum(ints.values()) / len(ints)), 4)
    pub = [v for i, v in ints.items() if out.nodes[i].category in PUBLIC]
    pri = [v for i, v in ints.items() if out.nodes[i].category in PRIVATE]
    if pub and pri:
        out.privacy_gradient = round((sum(pri) / len(pri)) / (sum(pub) / len(pub)), 4)
    return out


# ------------------------------------------------------------------ findings
def check(s: PlanSyntax) -> list[tuple[str, str, float, str, list[str]]]:
    """(rule_id, severity, weight, detail, element_ids)."""
    out = []
    if s.k < 3 or not s.nodes:
        return out

    if s.public_score <= 0 and s.living_relative:
        out.append(("SYNTAX.LIVING_NOT_CORE", "error", 0.9,
                    f"the most integrated space is {s.nodes[s.core].name} "
                    f"({s.nodes[s.core].category}), which is not a public space; "
                    f"public_score {s.public_score:+.3f} — the house is organised "
                    "around the wrong room", [s.core]))

    if 0 < s.living_relative < 1.10:
        out.append(("SYNTAX.WEAK_HIERARCHY", "warn", 0.6,
                    f"living-room integration is only {s.living_relative:.2f}x the "
                    "plan average; real layouts show markedly stronger dominance",
                    []))

    if s.privacy_gradient and s.privacy_gradient > 0.85:
        out.append(("SYNTAX.NO_PRIVACY_GRADIENT", "warn", 0.7,
                    f"private rooms are {s.privacy_gradient:.2f}x as integrated as "
                    "public ones; bedrooms should be markedly deeper than living "
                    "space, and this reads as one undifferentiated zone", []))

    # A private room that is more integrated than the plan average is being used
    # as circulation -- the formal version of "traffic passes through a bedroom".
    ints = [n.integration for n in s.nodes.values()]
    avg = sum(ints) / len(ints)
    for n in s.nodes.values():
        if n.category in PRIVATE and n.integration > avg * 1.05:
            out.append(("SYNTAX.PRIVATE_ROOM_INTEGRATED", "error", 0.8,
                        f"{n.name} has integration {n.integration:.2f} against a plan "
                        f"average of {avg:.2f} and connectivity {n.connectivity}; it is "
                        "functioning as circulation, not as a private room", [n.room_id]))
    return out
