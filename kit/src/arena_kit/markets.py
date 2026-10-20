"""The public market kinds: crowdbook scenarios that every game can use and every player can test
against offline.

A market kind turns a seed and a duration into a crowdbook scenario's text. The same text runs a
live round (`crowdbook serve`), a lockstep round and a local test (`crowdbook_learn.Pool`), so a
scenario has a `[participant]` section for the players' seats, `[scoring]` for live rounds and
`[learning]` for stepped ones. The server adds kinds of its own through the
`crowdbook_arena.market_kinds` entry point group; those are not in this package.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

SECOND = 1_000_000_000

# When players hear of a jump in the true value, if a kind has news: at once, with the experts
# (about two seconds late), with the crowd (about thirty), or never.
NEWS_DELAYS: dict[str, int | None] = {"instant": 0, "experts": 2 * SECOND, "crowd": 30 * SECOND,
                                      "off": None}


@dataclass(frozen=True)
class Jump:
    time: int       # simulated nanoseconds since the start
    change: float   # ticks


def headline(change: float) -> str:
    size = round(abs(change))
    if size == 0:
        return "Minor news: the value barely moves"
    return f"{'Good' if change > 0 else 'Bad'} news: value {'up' if change > 0 else 'down'} about {size}"


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

    # Optional, as `jumps` is: `briefing(seed, duration_s) -> str`, what players are told before a
    # round of this kind (who trades, the fees, the limits, the news and the score), without
    # anything of the seed's path. Kinds without one are briefed with their description.


def briefing_of(kind: MarketKind, seed: int, duration_s: int, news: str | None = None) -> str:
    """The briefing for a round of this kind: the kind's own `briefing`, or its description,
    and when headlines come out if the round has news (`news`, a key of NEWS_DELAYS)."""
    brief = getattr(kind, "briefing", None)
    text = str(brief(seed, duration_s)) if callable(brief) else kind.description
    if news is not None and has_news(kind):
        text = text.rstrip() + "\n\n" + _NEWS_LINES[news]
    return text


_NEWS_LINES = {
    "instant": "Headlines: each jump in the true value is told to you the moment it happens.",
    "experts": "Headlines: each jump in the true value is told to you about 2 seconds after it "
               "happens, as the experts hear it.",
    "crowd": "Headlines: each jump in the true value is told to you about 30 seconds after it "
             "happens, with the crowd, after the experts have traded on it.",
    "off": "Headlines: none this round. Watch the trades for jumps in the true value.",
}


def jumps_of(kind: MarketKind, seed: int, duration_s: int) -> list[Jump]:
    """The jumps in a kind's true value that make news, for kinds that have them (a `jumps`
    method with the same arguments as `scenario`); none for the rest."""
    jumps = getattr(kind, "jumps", None)
    return list(jumps(seed, duration_s)) if callable(jumps) else []


def has_news(kind: MarketKind) -> bool:
    has = getattr(kind, "has_news", None)
    return bool(has()) if callable(has) else callable(getattr(kind, "jumps", None))


def headlines(kind: MarketKind, seed: int, duration_s: int, delay: str = "experts") -> list:
    """The headlines players get in a round of this kind: one for each jump, `delay` after it
    (a key of NEWS_DELAYS), in time order."""
    from .bot import News

    if delay not in NEWS_DELAYS:
        raise ValueError(f"news delay: one of {', '.join(NEWS_DELAYS)}")
    lag = NEWS_DELAYS[delay]
    if lag is None:
        return []
    out = [News(j.time + lag, headline(j.change), round(j.change))
           for j in jumps_of(kind, seed, duration_s)]
    return [n for n in sorted(out, key=lambda n: n.time) if n.time <= duration_s * SECOND]


_VALUE_PATH: list[bool] = []


def engine_has_value_path() -> bool:
    """Whether the installed crowdbook takes a recorded `path` in [fundamental] (from 0.14)."""
    if not _VALUE_PATH:
        import crowdbook_learn

        probe = ('duration = "2s"\nreference_price = 100\n[fundamental]\npath = [[0, 100.0]]\n'
                 '[[agents]]\ntype = "zero_intelligence"\ncount = 1\n')
        try:
            crowdbook_learn.Pool.from_text(probe, markets=1, threads=1)
            _VALUE_PATH.append(True)
        except Exception:
            _VALUE_PATH.append(False)
    return _VALUE_PATH[0]


@dataclass(frozen=True)
class TemplateKind:
    """A kind whose scenario is a template with `{duration}` and `{seed}` in it."""

    name: str
    title: str
    description: str
    template: str
    brief: str = ""  # the briefing, with `{duration}` in it

    def scenario(self, seed: int, duration_s: int) -> str:
        return self.template.format(seed=int(seed), duration=f"{int(duration_s)}s")

    def briefing(self, seed: int, duration_s: int) -> str:
        return (self.brief or self.description).format(duration=_minutes(duration_s))


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


def _minutes(duration_s: int) -> str:
    m, s = divmod(int(duration_s), 60)
    if not m:
        return f"{s} seconds"
    return f"{m} minute{'s' * (m != 1)}" + (f" {s} seconds" if s else "")


def _brief(value: str, crowd: str) -> str:
    """A public kind's briefing: its true value and crowd, and the rules every public kind
    shares (set in _template and _SEAT)."""
    return (
        "The round lasts {duration}. One stock trades in a single order book, priced in ticks "
        "(1 tick = 1 cent) and starting near 10,000.\n\n"
        f"True value: {value}\n\n"
        f"Who trades: {crowd}\n\n"
        "Fees: a resting order that is filled earns 0.1 tick a lot; an order that takes from the "
        "book pays 0.2 tick a lot.\n\n"
        "Limits: at most 100 lots long or short and 20 lots an order, and no more than your "
        "game balance buys at the price, counting open orders.\n\n"
        "Score: your profit after fees, with what you hold at the end valued at what selling or "
        "buying it back through the book would bring. It is added to your game balance."
    )


_PEOPLE = ("two market makers quoting both sides (one fast, 5 lots every quarter second; one "
           "slower, 10 lots every second); {noise} noise traders who buy and sell at random; "
           "{trend} trend followers who buy rises and sell falls; two experts who see the true "
           "value {lag} late and trade when the price is 2 ticks or more away from it; and 30 "
           "followers who see it 30 seconds late, roughly.")


CALM = TemplateKind(
    name="calm",
    title="Calm",
    description=(
        "A few trades a second around a true value that wanders slowly. Two market makers, noise "
        "traders, trend followers, and traders who see the true value early or late."
    ),
    template=_template('\n[fundamental]\nvolatility = 0.5\nstep = "250ms"\n'),
    brief=_brief("a random walk, about half a tick a second; no jumps and no headlines.",
                 _PEOPLE.format(noise=60, trend=5, lag="1 second")),
)

@dataclass(frozen=True)
class NewsKind:
    """A market whose true value wanders and jumps on news. The value's path is drawn here, from
    the round's seed, and given to crowdbook as a recorded path, so that the jumps are known and
    can be told to players as headlines."""

    name: str
    title: str
    description: str
    template: str
    volatility: float = 0.3   # ticks per square root of a second
    step_s: float = 0.25
    jump_rate: float = 1 / 60  # a second
    jump_size: float = 8.0     # ticks, the standard deviation of a jump
    initial: float = 10000.0

    def _walk(self, seed: int, duration_s: int) -> tuple[list[tuple[float, float]], list[Jump]]:
        rng = random.Random(f"news-path:{seed}")
        value, path, jumps = self.initial, [(0.0, self.initial)], []
        p_jump = 1 - math.exp(-self.jump_rate * self.step_s)
        for i in range(1, int(duration_s / self.step_s) + 1):
            t = i * self.step_s
            value += rng.gauss(0, self.volatility * math.sqrt(self.step_s))
            if rng.random() < p_jump:
                change = rng.gauss(0, self.jump_size)
                value += change
                jumps.append(Jump(round(t * SECOND), change))
            path.append((t, value))
        return path, jumps

    fallback: str = ""        # the engine's own jumps, for a crowdbook without value paths
    brief: str = ""

    def briefing(self, seed: int, duration_s: int) -> str:
        return self.brief.format(duration=_minutes(duration_s))

    def has_news(self) -> bool:
        return engine_has_value_path()

    def scenario(self, seed: int, duration_s: int) -> str:
        if not self.has_news():
            return self.fallback.format(seed=int(seed), duration=f"{int(duration_s)}s")
        path, _ = self._walk(seed, duration_s)
        points = ", ".join(f"[{t:g}, {v:.3f}]" for t, v in path)
        return self.template.format(seed=int(seed), duration=f"{int(duration_s)}s",
                                    path=points)

    def jumps(self, seed: int, duration_s: int) -> list[Jump]:
        return self._walk(seed, duration_s)[1] if self.has_news() else []


NEWS = NewsKind(
    name="news",
    title="News",
    description=(
        "The true value jumps by about eight ticks without warning, about once a minute. Experts "
        "see the news a couple of seconds late, the crowd half a minute later. Players get a "
        "headline too, when the host chooses."
    ),
    template=_template('\n[fundamental]\npath = [{path}]\n', expert_lag="2s"),
    fallback=_template(
        '\n[fundamental]\nvolatility = 0.3\nstep = "250ms"\njump_rate = 0.0167\njump_size = 8.0\n',
        expert_lag="2s"),
    brief=_brief("a random walk of about a third of a tick a second, which jumps without "
                 "warning about once a minute, by about 8 ticks up or down.",
                 _PEOPLE.format(noise=60, trend=5, lag="2 seconds")),
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
    brief=_brief("a random walk of about a tick a second; no jumps and no headlines.",
                 _PEOPLE.format(noise=60, trend=15, lag="1 second")
                 + " The noise traders send market orders more often than in Calm, and the "
                   "trend followers push prices past the true value before they come back."),
)

PUBLIC_KINDS: dict[str, MarketKind] = {k.name: k for k in (CALM, NEWS, TRENDING)}


def public_kind(name: str) -> MarketKind:
    try:
        return PUBLIC_KINDS[name]
    except KeyError:
        raise KeyError(f"unknown market kind {name!r}; public kinds: "
                       f"{', '.join(sorted(PUBLIC_KINDS))}") from None


