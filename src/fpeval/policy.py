"""Every knob that controls agent behaviour, in one place.

Written as data because these are product decisions, not code decisions: whether
Vastu is pursued, how freely the agent asks questions, how much it may change
without confirmation. Scattering them through prompts makes them undiscoverable
and untestable; a dataclass makes them diffable, per-project, and assertable.

The two that matter most in practice:

* `vastu` -- a toggle, not a hardcoded objective. Roughly half of Indian clients
  want it enforced and half want it ignored, and a system that always pursues it
  wastes solver effort and patch calls on the second half.
* `ask_policy` -- how eagerly to raise clarifying questions. Measured on the LLM
  layer: opus asks a mean 4.68 questions even on fully determined briefs, and
  4/22 of those were blocking. Asking about everything is as bad a failure as
  inventing a plot size.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal

AskPolicy = Literal["never", "blocking_only", "liberal"]
Autonomy = Literal["read_only", "propose", "apply_safe", "apply_all"]
VastuMode = Literal["off", "advisory", "strict"]


@dataclass
class VastuConfig:
    """Vastu as configuration.

    `mode` off       -- findings suppressed entirely; not scored, not patched.
           advisory  -- reported and scored, but never blocks and never spends a
                        patch iteration of its own.
           strict    -- pursued: the repair loop keeps iterating while the score
                        is under `target_score`.
    `zones` overrides our default table, because practitioners disagree and a
    client's own consultant outranks us (suite case vastu-07 tests exactly that).
    """
    mode: VastuMode = "advisory"
    target_score: float = 0.78
    weight: float = 1.0
    brahmasthan_open: bool = True     # conflicts with efficiency on small plots
    zones: dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def pursued(self) -> bool:
        """Whether the repair loop should spend iterations on it."""
        return self.mode == "strict"


@dataclass
class AskConfig:
    """Clarifying-question behaviour.

    `blocking_only` is the default because it matches what a user tolerates: ask
    when proceeding would require inventing a fact (plot size, bedroom count),
    stay quiet when a sensible default exists (wall thickness, floor finish).
    """
    policy: AskPolicy = "blocking_only"
    max_questions: int = 2
    # Facts we will never invent. Missing any of these is always worth asking.
    never_assume: tuple[str, ...] = (
        "plot_dimensions", "bedroom_count", "unit_area", "north_direction",
    )
    # Facts we will always default rather than ask about.
    always_default: tuple[str, ...] = (
        "wall_thickness", "floor_finish", "ceiling_height", "door_widths",
        "window_sizes", "furniture_style",
    )


@dataclass
class WriteConfig:
    """How much the agent may change before it must ask."""
    autonomy: Autonomy = "apply_safe"
    # Above this many rooms touched in one batch, confirm first.
    confirm_over_rooms: int = 3
    # Geometry ops must name the finding they repair, or be refused.
    require_finding_citation: bool = True
    # Never silently move something the user has touched.
    respect_pins: bool = True
    allow_delete: bool = False


@dataclass
class LoopConfig:
    max_iters: int = 5
    solve_time_limit_s: float = 10.0
    # A patch that scores worse is discarded; this caps wasted calls.
    max_worse_in_a_row: int = 2


# Rule families, so a whole class can be switched without naming every id.
# Every prefix the validator actually emits. This list was stuck at the first
# six long after TOPO, SYNTAX, ZONE and BRIEF shipped, and because `allows()`
# defaults an unlisted family to on, those four could not be switched off at
# all -- the presets below silently kept them running.
FAMILIES = ("GEO", "NBC", "BYLAW", "VASTU", "DESIGN", "TYPO",
            "TOPO", "SYNTAX", "ZONE", "BRIEF")


@dataclass
class RuleConfig:
    """Which validators run, and how loudly.

    Switchable per family and per rule because these are not all the same kind of
    statement. GEO is arithmetic -- a room either overlaps another or it does
    not. NBC is law. VASTU is a client preference. DESIGN and TYPO are domain
    judgement, and judgement is exactly what a user should be able to turn off
    when they disagree with it.

    `severity` also lets a warning become an error, which is how a strict-Vastu
    client differs from an indifferent one without a second rule table.
    """
    typology: str = "auto"                       # or an explicit typology key
    families: dict[str, bool] = field(default_factory=lambda: {f: True for f in FAMILIES})
    disabled: tuple[str, ...] = ()               # exact rule ids, off entirely
    severity: dict[str, str] = field(default_factory=dict)   # id -> error|warn|off

    def family_of(self, rule_id: str) -> str:
        return (rule_id.split(".", 1)[0] or "").upper()

    def allows(self, rule_id: str) -> bool:
        if rule_id in self.disabled:
            return False
        if self.severity.get(rule_id) == "off":
            return False
        return self.families.get(self.family_of(rule_id), True)

    def severity_for(self, rule_id: str, default: str) -> str:
        return self.severity.get(rule_id, default)


@dataclass
class AgentPolicy:
    vastu: VastuConfig = field(default_factory=VastuConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    write: WriteConfig = field(default_factory=WriteConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    rules: RuleConfig = field(default_factory=RuleConfig)
    city_profile: str = "BENGALURU"
    # Relax NBC room-width minima where local practice differs. Bengaluru 20x30
    # houses are built with ~2.1 m rooms against NBC's 2.4 m; refusing them all
    # is less useful than building them and stating the deviation.
    relaxed_minima: bool = False
    style_pack: str = "default_in"

    def to_dict(self) -> dict: return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentPolicy":
        return cls(
            vastu=VastuConfig(**d.get("vastu", {})),
            ask=AskConfig(**{k: (tuple(v) if isinstance(v, list) else v)
                             for k, v in d.get("ask", {}).items()}),
            write=WriteConfig(**d.get("write", {})),
            loop=LoopConfig(**d.get("loop", {})),
            rules=RuleConfig(**d.get("rules", {})),
            city_profile=d.get("city_profile", "BENGALURU"),
            relaxed_minima=d.get("relaxed_minima", False),
            style_pack=d.get("style_pack", "default_in"),
        )


# Named presets, so a caller picks an intent rather than eleven fields.
PRESETS: dict[str, AgentPolicy] = {
    "default": AgentPolicy(),
    "vastu_strict": AgentPolicy(vastu=VastuConfig(mode="strict", weight=1.5)),
    "vastu_off": AgentPolicy(vastu=VastuConfig(mode="off")),
    # Small plots: pursue nothing soft, relax widths, ask nothing optional.
    "compact_plot": AgentPolicy(vastu=VastuConfig(mode="advisory"),
                                relaxed_minima=True,
                                ask=AskConfig(policy="blocking_only", max_questions=1)),
    # Review mode: the agent may look and propose but never mutate.
    "read_only": AgentPolicy(write=WriteConfig(autonomy="read_only")),
    # Concept stage: only hard geometry and law. Domain judgement and Vastu are
    # noise when the client is still deciding how many bedrooms they want.
    "concept": AgentPolicy(rules=RuleConfig(
        families={"GEO": True, "NBC": True, "BYLAW": True, "BRIEF": True,
                  "VASTU": False, "DESIGN": False, "TYPO": False,
                  "TOPO": False, "SYNTAX": False, "ZONE": False})),
    # Everything on, and circulation judgement promoted to blocking.
    "strict_review": AgentPolicy(rules=RuleConfig(severity={
        "DESIGN.DEAD_END_CIRCULATION": "error",
        "DESIGN.TOO_DEEP": "error",
        "TYPO.MISSING_ADJACENCY": "error"})),
    "autonomous": AgentPolicy(vastu=VastuConfig(mode="strict"),
                              write=WriteConfig(autonomy="apply_all",
                                                confirm_over_rooms=99),
                              ask=AskConfig(policy="never")),
}
