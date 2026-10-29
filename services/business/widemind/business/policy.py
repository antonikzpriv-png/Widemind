"""Operator-owned configuration, not instructions accepted from customer content."""
from dataclasses import asdict, dataclass, field


def integer(value, name, minimum=0, maximum=10**12):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class Policy:
    capabilities: tuple[str, ...] = ("research",)
    goals: tuple[str, ...] = ("Deliver useful, sourced work within budget",)
    experiment_budget_micro_usd: int = 1_000_000
    max_job_micro_usd: int = 250_000
    prices: dict[str, int] = field(default_factory=lambda: {"research-brief": 2_000_000})
    turnaround_seconds: int = 86400
    quote_valid_seconds: int = 172800
    allow_outbound: bool = False
    max_open_orders: int = 10
    priority_weight: int = 10
    margin_weight: int = 1
    goal_weights: dict[str, int] = field(default_factory=lambda: {"research-brief": 1})
    conversation_enabled: bool = True
    conversation_model_max_micro_usd: int = 100_000
    conversation_run_budget_micro_usd: int = 1_000_000
    conversation_max_turns: int = 20
    conversation_followups: int = 2
    conversation_followup_seconds: int = 86400
    conversation_quiet_start_hour: int = 20
    conversation_quiet_end_hour: int = 8
    conversation_price_floors: dict[str, int] = field(default_factory=dict)
    conversation_revision_allowance: int = 0
    conversation_model_max_output_tokens: int = 2000

    def __post_init__(self):
        for key in ("experiment_budget_micro_usd", "max_job_micro_usd", "priority_weight", "margin_weight"):
            integer(getattr(self, key), key)
        for key in ("turnaround_seconds", "quote_valid_seconds", "max_open_orders"):
            integer(getattr(self, key), key, 1)
        if type(self.allow_outbound) is not bool:
            raise ValueError("allow_outbound must be boolean")
        if not isinstance(self.capabilities, (tuple, list)) or not isinstance(self.goals, (tuple, list)):
            raise ValueError("capabilities and goals must be lists")
        if not isinstance(self.prices, dict) or not isinstance(self.goal_weights, dict):
            raise ValueError("prices and goal weights must be mappings")
        if not self.goals or any(not isinstance(g, str) or not g.strip() for g in self.goals):
            raise ValueError("business goals required")
        if any(not isinstance(c, str) or not c for c in self.capabilities):
            raise ValueError("invalid capabilities")
        for service, price in self.prices.items():
            if not isinstance(service, str) or not service:
                raise ValueError("invalid service price")
            integer(price, "price", 1)
        for weight in self.goal_weights.values():
            integer(weight, "goal weight")
        if type(self.conversation_enabled) is not bool:
            raise ValueError("conversation_enabled must be boolean")
        integer(self.conversation_model_max_micro_usd, "conversation model maximum", 1, 10**9)
        integer(self.conversation_run_budget_micro_usd, "conversation run budget", 0, 10**12)
        integer(self.conversation_max_turns, "conversation turns", 1, 100)
        integer(self.conversation_followups, "conversation followups", 0, 3)
        integer(self.conversation_followup_seconds, "conversation followup interval", 300, 30 * 86400)
        integer(self.conversation_quiet_start_hour, "conversation quiet start hour", 0, 23)
        integer(self.conversation_quiet_end_hour, "conversation quiet end hour", 0, 23)
        integer(self.conversation_revision_allowance, "conversation revisions", 0, 3)
        integer(self.conversation_model_max_output_tokens, "conversation output tokens", 128, 8192)
        if not isinstance(self.conversation_price_floors, dict):
            raise ValueError("conversation_price_floors must be a mapping")
        for service, floor in self.conversation_price_floors.items():
            if service not in self.prices:
                raise ValueError("conversation floor requires a configured service price")
            integer(floor, "conversation price floor", 10_000)
            if floor % 10_000 or floor > self.prices[service]:
                raise ValueError("conversation price floor must be whole USD cents within the configured price")

    def document(self):
        return asdict(self)
