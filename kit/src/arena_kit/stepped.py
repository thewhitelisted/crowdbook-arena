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

from .bot import Bot, Fill, News, Quote, State, Trade
from . import money
from .markets import MarketKind, briefing_of, has_news, headlines, public_kind
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
                          position, at)

        resting = self._resting()
        taken = "buy" if q.take > 0 else "sell" if q.take < 0 else None

        def cut(side: str, level, rests):
            if level is None or side == taken:
                return None  # a take on a side leaves no quote there this turn
            size = min(level[1], money.room(side, position, 0, eq, level[0]))
            if rests is not None and rests[0] == level[0]:
                # The engine leaves an order at the same price as it is, size and all: keep it
                # only if the bankroll still carries it.
                return rests if rests[1] <= money.room(side, position, 0, eq, level[0]) else None
            return (level[0], size) if size > 0 else None

        take = q.take
        if take:
            price = q.take_price or (ask if take > 0 else bid)
            take = min(abs(take), money.room(taken, position, 0, eq, price)) if price else 0
            take = take if q.take > 0 else -take
        return Quote(bid=cut("buy", q.bid, resting.bid), ask=cut("sell", q.ask, resting.ask),
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


@dataclass
class LocalResult:
    market: str
    seed: int
    score: float
    turns: int
    position: int
    fees: float


def run_local(bot: Bot, market: str | MarketKind = "calm", seed: int = 1,
              duration_s: int = 300, news: str = "experts",
              bankroll: float = DEFAULT_BANKROLL / money.DOLLAR) -> LocalResult:
    """Runs `bot` in a public market kind (or any `MarketKind`) on this machine, as a lockstep
    round would run it on the server. `news` is when headlines come, in markets with news:
    instant, experts, crowd or off, as the host of a round chooses. `bankroll` is the dollars
    the bot starts with, which sets its buying power as in a game."""
    kind = public_kind(market) if isinstance(market, str) else market
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
