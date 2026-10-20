"""Markets stepped one decision at a time, through crowdbook's learning pool: how a bot is tested
offline, and how the server runs lockstep rounds.

Each `SteppedMarket` is one market with one seat in it. The market waits at every decision (each
simulated second, and whenever one of the seat's orders fills) for the seat's `Quote`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

import crowdbook_learn

from .bot import Bot, Fill, News, Portfolio, Quote, State, Trade, checked_quotes
from . import money
from .markets import MarketKind, briefing_of, has_news, headlines, is_linked, public_kind
from .money import DEFAULT_BANKROLL

_S = {name: i for i, name in enumerate(crowdbook_learn.STATE_NAMES)}
_TRADE, _FILL = 1, 4


class SteppedMarket:
    """One market from a scenario's text, stepped by `step` until `done`."""

    def __init__(self, scenario: str, seed: int, news: list[News] | tuple = (),
                 bankroll: float = DEFAULT_BANKROLL):
        self.seed = int(seed)
        self._pool = crowdbook_learn.Pool.from_text(scenario, markets=1, threads=1)
        self._obs, self._info = self._pool.reset(seeds=[self.seed])
        self.done = False
        self.turns = 0
        self.bankroll = float(bankroll)  # tick-lots the seat starts with
        self._news = sorted(news, key=lambda n: n.time)  # headlines, by when they come out
        self._news_given = 0
        # In a round with several assets: the others' P&L, and what they tie up of the bankroll.
        self.extra_equity = 0.0
        self.other_committed = 0.0

    def state(self) -> State:
        """The seat's state at the current decision, with what it heard since the last one."""
        obs = self._obs
        s = obs["state"][0]
        news = []
        while (self._news_given < len(self._news)
               and self._news[self._news_given].time <= int(s[_S["time"]])):
            news.append(self._news[self._news_given])
            self._news_given += 1
        # Everything heard since the last decision, the latest rows kept if there were more.
        count = min(int(obs["counts"][0][0]), len(obs["events"][0]))
        rows = obs["events"][0][:count]
        trades, fills = [], []
        for row in rows:
            kind, t, a, b, c, d, e = (int(x) for x in row)
            if kind == _TRADE:
                trades.append(Trade(t, a, b, "buy" if c > 0 else "sell"))
            elif kind == _FILL:
                fills.append(Fill(t, "buy" if a > 0 else "sell", b, c, e / 1000))

        book = obs["book"][0]
        bids = [(int(p), int(q)) for p, q in book[0] if q > 0]
        asks = [(int(p), int(q)) for p, q in book[1] if q > 0]
        my_bid = my_ask = None
        for side, price, leaves, _ack, _cancel in obs["orders"][0]:
            if leaves > 0 and side > 0:
                my_bid = (int(price), int(leaves))
            elif leaves > 0 and side < 0:
                my_ask = (int(price), int(leaves))
        bid = (int(s[_S["bid_price"]]), int(s[_S["bid_quantity"]])) if s[_S["bid_price"]] else None
        ask = (int(s[_S["ask_price"]]), int(s[_S["ask_quantity"]])) if s[_S["ask_price"]] else None
        last = int(s[_S["last_trade"]]) or None
        return State(
            time=int(s[_S["time"]]), time_left=int(s[_S["time_left"]]),
            position=int(s[_S["position"]]), cash=float(s[_S["cash"]]),
            fees=float(s[_S["fees"]]) / 1000, bid=bid, ask=ask, last_trade=last,
            bids=bids, asks=asks, my_bid=my_bid, my_ask=my_ask,
            max_position=int(s[_S["max_position"]]),
            max_order_quantity=int(s[_S["max_order_quantity"]]),
            reference_price=int(s[_S["reference_price"]]), trades=trades, fills=fills,
            news=news, balance=self.bankroll)

    def step(self, quote: Quote | None) -> None:
        """Places the quote, or leaves the seat's orders as they are for None, and runs on to
        the next decision."""
        if self.done:
            raise RuntimeError("the market has ended")
        quote = self._resting() if quote is None else self._afford(quote.checked())
        row = [*(quote.bid or (0, 0)), *(quote.ask or (0, 0)), quote.take, quote.take_price or 0]
        self._obs, _rewards, terminated, truncated, self._info = self._pool.step_orders(
            np.array([row], dtype=np.int64))
        self.turns += 1
        self.done = bool(terminated[0] or truncated[0])

    def _afford(self, q: Quote) -> Quote:
        """The quote cut to the seat's buying power (arena_kit.money), as a live seat's is."""
        s = self._obs["state"][0]
        position = int(s[_S["position"]])
        bid, ask = int(s[_S["bid_price"]]), int(s[_S["ask_price"]])
        at = money.mark(bid, ask, int(s[_S["last_trade"]]), int(s[_S["reference_price"]]))
        eq = money.equity(self.bankroll, float(s[_S["cash"]]), float(s[_S["fees"]]) / 1000,
                          position, at) + self.extra_equity - self.other_committed

        taken = "buy" if q.take > 0 else "sell" if q.take < 0 else None

        def cut(side: str, level):
            if level is None or side == taken:
                return None  # a take on a side leaves no quote there this turn
            size = min(level[1], money.room(side, position, 0, eq, level[0]))
            return (level[0], size) if size > 0 else None

        take = q.take
        if take:
            price = q.take_price or (ask if take > 0 else bid)
            take = min(abs(take), money.room(taken, position, 0, eq, price)) if price else 0
            take = take if q.take > 0 else -take
        return Quote(bid=cut("buy", q.bid), ask=cut("sell", q.ask),
                     take=take, take_price=q.take_price)

    def _resting(self) -> Quote:
        bid = ask = None
        for side, price, leaves, _ack, _cancel in self._obs["orders"][0]:
            if leaves > 0:
                if side > 0:
                    bid = (int(price), int(leaves))
                else:
                    ask = (int(price), int(leaves))
        return Quote(bid=bid, ask=ask)

    @property
    def score(self) -> float:
        """The seat's score in tick-lots: its P&L after fees with its position closed through the
        book, as the market would end now."""
        return float(self._info["score"][0]) / 1000

    @property
    def value(self) -> float:
        """The market's true value now, for recordings; never shown to a bot."""
        return float(self._info["value"][0])

    @property
    def info(self) -> dict[str, Any]:
        return {"seed": self.seed, "turns": self.turns}

    def session(self) -> str:
        """The run as a crowdbook session file, for `crowdbook replay` and `crowdbook report`."""
        return self._pool.session(0)


class SteppedPortfolio:
    """Several markets, one for each asset of a linked kind, stepped together through one
    bankroll. Their decisions are taken in time order: at each turn the markets due to decide now
    get the bot's quotes, and the others show what they showed last, as of a moment no later than
    now, so that no market is seen ahead of another."""

    def __init__(self, scenarios: dict[str, str], seed: int,
                 bankroll: float = DEFAULT_BANKROLL):
        self.seed = int(seed)
        self.bankroll = float(bankroll)
        self.markets = {sym: SteppedMarket(text, seed, (), bankroll)
                        for sym, text in scenarios.items()}
        self.symbols = tuple(self.markets)
        self._next = {sym: m.state() for sym, m in self.markets.items()}  # each one's next turn
        self._shown: dict[str, State] = {}
        self.turns = 0
        self._cache: tuple[int, Portfolio] | None = None

    @property
    def done(self) -> bool:
        return all(m.done for m in self.markets.values())

    def _due(self) -> list[str]:
        live = [sym for sym, m in self.markets.items() if not m.done]
        if not live:
            return []
        now = min(self._next[sym].time for sym in live)
        return [sym for sym in live if self._next[sym].time == now]

    def state(self) -> Portfolio:
        """The portfolio at the current turn: the assets due now as they are, the others as last
        shown, without the trades and fills already reported."""
        if self._cache and self._cache[0] == self.turns:
            return self._cache[1]
        due = self._due() or list(self.symbols)
        assets = {}
        for sym in self.symbols:
            if sym in due or sym not in self._shown:
                assets[sym] = self._next[sym]
            else:
                old = self._shown[sym]
                assets[sym] = State(**{**old.__dict__, "trades": [], "fills": [], "news": []})
        now = max(self._next[sym].time for sym in due)
        p = Portfolio(time=now, time_left=min(a.time_left for a in assets.values()),
                      assets=assets, balance=self.bankroll)
        self._cache = (self.turns, p)
        return p

    def step(self, quotes: dict[str, Quote] | None) -> None:
        """Places the due markets' quotes (an asset left out keeps its quotes) and runs on to
        the next turn."""
        if self.done:
            raise RuntimeError("the market has ended")
        p = self.state()
        due = self._due()
        tied = {sym: p.committed(sym) for sym in self.symbols}
        for sym in due:
            m = self.markets[sym]
            m.extra_equity = sum(a.equity - a.balance for o, a in p.assets.items() if o != sym)
            m.other_committed = sum(v for o, v in tied.items() if o != sym)
            q = (quotes or {}).get(sym)
            if q is not None:
                # What this asset will tie up once its quote is in, for the assets after it.
                q = m._afford(q.checked())
                a = p.assets[sym]
                tied[sym] = money.committed(a.position + q.take, q.bid[1] if q.bid else 0,
                                            q.ask[1] if q.ask else 0, p.mark(sym))
            m.step(q)
            self._next[sym] = m.state()
        self._shown = dict(p.assets)
        self.turns += 1

    @property
    def score(self) -> float:
        return sum(m.score for m in self.markets.values())

    @property
    def positions(self) -> dict[str, int]:
        return {sym: int(m._obs["state"][0][_S["position"]]) for sym, m in self.markets.items()}

    @property
    def info(self) -> dict[str, Any]:
        return {"seed": self.seed, "turns": self.turns}


@dataclass
class LocalResult:
    market: str
    seed: int
    score: float
    turns: int
    position: int
    fees: float
    positions: dict[str, int] | None = None  # by asset, in a round with several


def run_local(bot: Bot, market: str | MarketKind = "calm", seed: int = 1,
              duration_s: int = 300, news: str = "experts",
              bankroll: float = DEFAULT_BANKROLL / money.DOLLAR) -> LocalResult:
    """Runs `bot` in a public market kind (or any `MarketKind`) on this machine, as a lockstep
    round would run it on the server. `news` is when headlines come, in markets with news:
    instant, experts, crowd or off, as the host of a round chooses. `bankroll` is the dollars
    the bot starts with, which sets its buying power as in a game."""
    kind = public_kind(market) if isinstance(market, str) else market
    if is_linked(kind):
        return _run_linked(bot, kind, seed, duration_s, bankroll)
    m = SteppedMarket(kind.scenario(seed, duration_s), seed,
                      headlines(kind, seed, duration_s, news), bankroll * money.DOLLAR)
    first = m.state()
    bot.on_start({"market": kind.name, "seed": seed, "duration_s": duration_s, "mode": "local",
                  "briefing": briefing_of(kind, seed, duration_s,
                                          news if has_news(kind) else None),
                  "max_position": first.max_position,
                  "max_order_quantity": first.max_order_quantity})
    state = first
    while True:
        m.step(bot.on_turn(state))
        state = m.state()
        if m.done:
            break
    result = LocalResult(market=kind.name, seed=seed, score=m.score, turns=m.turns,
                         position=state.position, fees=state.fees)
    bot.on_end({"score": result.score, "position": result.position, "turns": result.turns})
    return result


def _run_linked(bot: Bot, kind: Any, seed: int, duration_s: int,
                bankroll: float) -> LocalResult:
    m = SteppedPortfolio(kind.scenarios(seed, duration_s), seed, bankroll * money.DOLLAR)
    first = m.state()
    bot.on_start({"market": kind.name, "seed": seed, "duration_s": duration_s, "mode": "local",
                  "briefing": briefing_of(kind, seed, duration_s),
                  "assets": {a.symbol: a.title for a in kind.assets},
                  "max_position": next(iter(first.assets.values())).max_position,
                  "max_order_quantity": next(iter(first.assets.values())).max_order_quantity})
    while not m.done:
        m.step(checked_quotes(bot.on_turn(m.state()), m.symbols))
    fees = sum(float(mk._obs["state"][0][_S["fees"]]) / 1000 for mk in m.markets.values())
    result = LocalResult(market=kind.name, seed=seed, score=m.score, turns=m.turns,
                         position=sum(m.positions.values()), fees=fees, positions=m.positions)
    bot.on_end({"score": result.score, "positions": result.positions, "turns": result.turns})
    return result

