"""The public market kinds: crowdbook scenarios that every game can use and every player can test
against offline.

A market kind turns a seed and a duration into a crowdbook scenario's text. The same text runs a
live round (`crowdbook serve`), a lockstep round and a local test (`crowdbook_learn.Pool`), so a
scenario has a `[participant]` section for the players' seats, `[scoring]` for live rounds and
`[learning]` for stepped ones. The server adds kinds of its own through the
`crowdbook_arena.market_kinds` entry point group; those are not in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class MarketKind(Protocol):
    """What the server needs from a kind of market."""

    name: str  # short, unique: letters, digits, '-' and '_'
    title: str
    description: str

    def scenario(self, seed: int, duration_s: int) -> str:
        """The scenario's text for a round with this seed, lasting `duration_s` simulated
        seconds."""
        ...


@dataclass(frozen=True)
class TemplateKind:
    """A kind whose scenario is a template with `{duration}` and `{seed}` in it."""

    name: str
    title: str
    description: str
    template: str

    def scenario(self, seed: int, duration_s: int) -> str:
        return self.template.format(seed=int(seed), duration=f"{int(duration_s)}s")


# The seat every player trades from, the scoring of live rounds, and the decisions of stepped
# ones. Live and stepped rounds both value a position at what closing it through the book would
# bring, after fees, so the two kinds of round score alike.
_SEAT = """
[participant]
latency = {{ to_exchange = "1ms", from_exchange = "1ms" }}
account = {{ max_position = {max_position}, max_order_quantity = {max_order} }}

[scoring]
mark = "book"

[learning]
interval = "1s"
warmup = "1s"
depth_levels = 10
close = "book"
reward_mark = "book"
"""

_CROWD = """
[[agents]]
type = "zero_intelligence"
name = "noise"
count = {noise}
limit_rate = 0.5
market_rate = {market_rate}
cancel_rate = 0.1
max_offset = 8
latency = {{ to_exchange = "300us", from_exchange = "300us", jitter = "100us" }}
account = {{ max_position = 200, max_order_quantity = 10 }}

[[agents]]
type = "market_maker"
name = "maker"
intensity = 1.0
quote_size = 5
max_inventory = 100
requote_interval = "250ms"
post_only = true
latency = {{ to_exchange = "20us", from_exchange = "20us" }}
account = {{ max_position = 110, max_order_quantity = 10 }}

[[agents]]
type = "market_maker"
name = "slow maker"
intensity = 0.5
quote_size = 10
max_inventory = 100
requote_interval = "1s"
post_only = true
latency = {{ to_exchange = "200us", from_exchange = "200us" }}
account = {{ max_position = 110, max_order_quantity = 10 }}

[[agents]]
type = "momentum"
name = "trend"
count = {trend}
interval = "500ms"
fast_half_life = "2s"
slow_half_life = "20s"
threshold = 1.0
order_size = 3
max_position = 30
latency = {{ to_exchange = "500us", from_exchange = "500us", jitter = "200us" }}
account = {{ max_position = 33, max_order_quantity = 3 }}

[[agents]]
type = "informed"
name = "experts"
count = 2
interval = "2s"
noise = 1.0
error = 1.0
error_memory = "300s"
lag = "{expert_lag}"
threshold = 2.0
order_size = 3
max_position = 300
latency = {{ to_exchange = "200us", from_exchange = "200us", jitter = "50us" }}
account = {{ max_position = 303, max_order_quantity = 3 }}

[[agents]]
type = "informed"
name = "followers"
count = 30
interval = "5s"
noise = 1.0
error = 3.0
error_memory = "300s"
lag = "30s"
threshold = 4.0
order_size = 3
max_position = 200
latency = {{ to_exchange = "200us", from_exchange = "200us", jitter = "50us" }}
account = {{ max_position = 203, max_order_quantity = 3 }}
"""


def _template(fundamental: str, *, noise: int = 60, market_rate: float = 0.05, trend: int = 5,
              expert_lag: str = "1s", max_position: int = 100, max_order: int = 20) -> str:
    head = 'seed = {seed}\nduration = "{duration}"\nreference_price = 10000\n'
    exchange = "\n[exchange]\ndepth_levels = 10\nmaker_fee = -0.1\ntaker_fee = 0.2\n"
    seat = _SEAT.format(max_position=max_position, max_order=max_order)
    crowd = _CROWD.format(noise=noise, market_rate=market_rate, trend=trend,
                          expert_lag=expert_lag)
    # The seat and crowd are formatted now; only {seed} and {duration} are left for the round.
    seat = seat.replace("{", "{{").replace("}", "}}")
    crowd = crowd.replace("{", "{{").replace("}", "}}")
    return head + fundamental + exchange + seat + crowd


CALM = TemplateKind(
    name="calm",
    title="Calm",
    description=(
        "A few trades a second around a true value that wanders slowly. Two market makers, noise "
        "traders, trend followers, and traders who see the true value early or late."
    ),
    template=_template('\n[fundamental]\nvolatility = 0.5\nstep = "250ms"\n'),
)

NEWS = TemplateKind(
    name="news",
    title="News",
    description=(
        "The true value jumps by about eight ticks without warning, about once a minute. Experts "
        "see the news a couple of seconds late, the crowd half a minute later."
    ),
    template=_template(
        '\n[fundamental]\nvolatility = 0.3\nstep = "250ms"\njump_rate = 0.0167\njump_size = 8.0\n',
        expert_lag="2s",
    ),
)

TRENDING = TemplateKind(
    name="trending",
    title="Trending",
    description=(
        "A livelier true value and a pack of trend followers who chase every move, so prices "
        "overshoot and come back."
    ),
    template=_template('\n[fundamental]\nvolatility = 1.0\nstep = "250ms"\n', trend=15,
                       market_rate=0.08),
)

PUBLIC_KINDS: dict[str, MarketKind] = {k.name: k for k in (CALM, NEWS, TRENDING)}


def public_kind(name: str) -> MarketKind:
    try:
        return PUBLIC_KINDS[name]
    except KeyError:
        raise KeyError(f"unknown market kind {name!r}; public kinds: "
                       f"{', '.join(sorted(PUBLIC_KINDS))}") from None


