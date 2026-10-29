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

    def document(self):
        return asdict(self)
