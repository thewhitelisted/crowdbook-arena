"""What a bot sees and what it answers, the same offline, in lockstep rounds and in live ones.

At each turn a bot gets a `State` and answers with a `Quote`: the bid and the offer it wants
resting in the book, and a quantity to take from it now. It keeps at most one order a side; a
quote at the same price as the one resting keeps its place in the queue, a quote at another price
moves it, and `None` takes it out. Answering `None` instead of a `Quote` leaves everything as it
is. Prices are in ticks, quantities in lots, cash and P&L in tick-lots.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import money
from .money import DEFAULT_BANKROLL

Level = tuple[int, int]  # (price, quantity)


@dataclass(frozen=True)
class Quote:
    bid: Level | None = None   # (price, size) to rest on the bid, or None for no bid
    ask: Level | None = None   # (price, size) to rest on the offer, or None for no offer
    take: int = 0              # lots to buy now (positive) or sell (negative), immediate-or-cancel
    take_price: int | None = None  # the worst price to take at; the best on the other side if None

    def checked(self) -> "Quote":
        """The quote with plain whole numbers (NumPy integers and whole floats are converted).
        Raises ValueError for a price or size that is not a positive whole number, the same
        offline as in a game."""
        return Quote(bid=_level(_whole(self.bid)), ask=_level(_whole(self.ask)),
                     take=_int(_whole(self.take), "take"),
                     take_price=_opt_int(_whole(self.take_price), "take_price"))

    def to_json(self) -> dict[str, Any]:
        return {"bid": list(self.bid) if self.bid else None,
                "ask": list(self.ask) if self.ask else None,
                "take": int(self.take), "take_price": self.take_price}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Quote":
        """A quote from a client's message. Raises ValueError for anything malformed."""
        if not isinstance(data, dict):
            raise ValueError("a quote is an object")
        unknown = set(data) - {"type", "turn", "bid", "ask", "take", "take_price"}
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(bid=_level(data.get("bid")), ask=_level(data.get("ask")),
                   take=_int(data.get("take", 0), "take"),
                   take_price=_opt_int(data.get("take_price"), "take_price"))


def _whole(value: Any) -> Any:
    """Whole numbers of any numeric type as int; anything else as it is, for the checks."""
    if isinstance(value, (list, tuple)):
        return [_whole(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    try:
        if int(value) == value:
            return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    return value


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) >= 2**62:
        raise ValueError(f"{name} must be a whole number")
    return value


def _opt_int(value: Any, name: str) -> int | None:
    return None if value is None else _int(value, name)


def _level(value: Any) -> Level | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("a quote's side is [price, size] or null")
    price, size = _int(value[0], "price"), _int(value[1], "size")
    if price <= 0 or size <= 0:
        raise ValueError("a quote's price and size must be positive")
    return (price, size)


@dataclass(frozen=True)
class Trade:
    time: int        # simulated nanoseconds since the start
    price: int
    quantity: int
    aggressor: str   # "buy" or "sell": the side that crossed the spread


@dataclass(frozen=True)
class Fill:
    time: int
    side: str        # "buy" or "sell"
    price: int
    quantity: int
    fee: float       # tick-lots, negative for a rebate


@dataclass(frozen=True)
class News:
    time: int        # simulated nanoseconds since the start, when the headline came out
    headline: str    # e.g. "Good news: value up about 8"
    change: int      # the jump in the true value, in ticks, rounded to a whole tick


@dataclass
class State:
    time: int                     # simulated nanoseconds since the start
    time_left: int
    position: int
    cash: float                   # change since the start, tick-lots
    fees: float                   # paid since the start, tick-lots, net of rebates
    bid: Level | None             # best bid and offer, as the seat has heard them
    ask: Level | None
    last_trade: int | None
    bids: list[Level]             # the book, best first
    asks: list[Level]
    my_bid: Level | None          # this seat's resting orders: (price, open quantity)
    my_ask: Level | None
    max_position: int
    max_order_quantity: int
    reference_price: int
    trades: list[Trade] = field(default_factory=list)  # since the last turn
    fills: list[Fill] = field(default_factory=list)    # this seat's, since the last turn
    news: list[News] = field(default_factory=list)     # headlines since the last turn
    balance: float = DEFAULT_BANKROLL  # tick-lots (cents) the seat started the round with

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask:
            return (self.bid[0] + self.ask[0]) / 2
        return None

    @property
    def equity(self) -> float:
        """The balance, plus the round's cash and position at the middle of the market, less
        fees: what buying power is measured against (arena_kit.money)."""
        return money.equity(self.balance, self.cash, self.fees, self.position,
                            money.mark(self.bid and self.bid[0], self.ask and self.ask[0],
                                       self.last_trade, self.reference_price))

    def room(self, side: str, price: float, open_lots: int = 0) -> int:
        """How many more lots this seat may bid ("buy") or offer ("sell") at `price`."""
        return money.room(side, self.position, open_lots, self.equity, price)

    def pnl(self, mark: float | None = None) -> float:
        """Cash plus the position at `mark` (the mid, else the last trade), less fees."""
        if mark is None:
            mark = self.mid or self.last_trade or self.reference_price
        return self.cash + self.position * mark - self.fees

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "State":
        d = dict(data)
        for key in ("bid", "ask", "my_bid", "my_ask"):
            d[key] = tuple(d[key]) if d.get(key) else None
        d["bids"] = [tuple(x) for x in d.get("bids", [])]
        d["asks"] = [tuple(x) for x in d.get("asks", [])]
        d["trades"] = [Trade(**t) for t in d.get("trades", [])]
        d["fills"] = [Fill(**f) for f in d.get("fills", [])]
        d["news"] = [News(**n) for n in d.get("news", [])]
        return cls(**d)


@dataclass
class Portfolio:
    """What a bot sees at a turn of a round with several linked assets (a pair, a basket): each
    asset's `State`, by symbol, and the one bankroll they share. A bot answers with a dict of
    `Quote`s by symbol; an asset left out keeps its quotes as they are.

    Every asset's State has the round's starting balance in `balance`, and its own P&L only in
    `equity`: the portfolio's `equity` counts them all, and `room` is the buying power the rules
    give on one asset, given what the others commit (arena_kit.money)."""

    time: int
    time_left: int
    assets: dict[str, State]
    balance: float = DEFAULT_BANKROLL  # tick-lots the bankroll started the round with

    @property
    def equity(self) -> float:
        return self.balance + sum(s.equity - s.balance for s in self.assets.values())

    @property
    def news(self) -> list[News]:
        return [n for s in self.assets.values() for n in s.news]

    def mark(self, symbol: str) -> float:
        s = self.assets[symbol]
        return money.mark(s.bid and s.bid[0], s.ask and s.ask[0], s.last_trade, s.reference_price)

    def committed(self, symbol: str) -> float:
        """What this asset's position and resting quotes tie up of the bankroll."""
        s = self.assets[symbol]
        return money.committed(s.position, s.my_bid[1] if s.my_bid else 0,
                               s.my_ask[1] if s.my_ask else 0, self.mark(symbol))

    def room(self, symbol: str, side: str, price: float, open_lots: int = 0) -> int:
        """How many more lots of this asset may be bid ("buy") or offered ("sell") at `price`."""
        others = sum(self.committed(o) for o in self.assets if o != symbol)
        s = self.assets[symbol]
        return money.room(side, s.position, open_lots, self.equity - others, price)

    def to_json(self) -> dict[str, Any]:
        return {"time": self.time, "time_left": self.time_left, "balance": self.balance,
                "assets": {k: v.to_json() for k, v in self.assets.items()}}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Portfolio":
        return cls(time=data["time"], time_left=data["time_left"], balance=data["balance"],
                   assets={k: State.from_json(v) for k, v in data["assets"].items()})


def quotes_from_json(data: Any, symbols: list[str] | tuple[str, ...]) -> dict[str, "Quote"]:
    """A multi-asset answer, {"quotes": {symbol: quote}}, checked: raises ValueError for an
    unknown symbol or a malformed quote. Symbols left out keep their quotes."""
    if not isinstance(data, dict) or not isinstance(data.get("quotes"), dict):
        raise ValueError('a multi-asset answer is {"quotes": {symbol: quote}}')
    unknown = set(data) - {"type", "turn", "quotes"}
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    out = {}
    for symbol, q in data["quotes"].items():
        if symbol not in symbols:
            raise ValueError(f"unknown asset {symbol!r}")
        out[symbol] = Quote.from_json(q)
    return out


def checked_quotes(answer: Any, symbols: tuple[str, ...]) -> dict[str, Quote] | None:
    """A bot's answer in a round with several assets: None, or a dict of Quotes by symbol,
    checked as a single Quote is. Raises ValueError for anything else."""
    if answer is None:
        return None
    if not isinstance(answer, dict):
        raise ValueError("in a round with several assets, answer with a dict of Quotes by "
                         "symbol")
    out = {}
    for sym, q in answer.items():
        if sym not in symbols:
            raise ValueError(f"unknown asset {sym!r}; the assets are {', '.join(symbols)}")
        if q is not None:
            out[sym] = q.checked()
    return out


class Bot:
    """Subclass this and override `on_turn`."""

    def on_start(self, info: dict[str, Any]) -> None:
        """Called once before the first turn, with the round's market and limits."""

    def on_turn(self, state: State) -> Quote | None:
        """The quotes to rest and what to take now; None leaves everything as it is. In a
        round with several assets, `state` is a Portfolio, and the answer a dict of Quotes by
        symbol."""
        return None

    def on_end(self, result: dict[str, Any]) -> None:
        """Called once at the end with the round's result for this seat."""


def bot_from_module(module: Any) -> Bot:
    """The bot a bot file defines: `bot`, an instance of Bot, or else its one Bot subclass, made
    with no arguments. Raises ValueError for a file that defines neither or several."""
    import inspect

    if isinstance(getattr(module, "bot", None), Bot):
        return module.bot
    classes = [c for _, c in inspect.getmembers(module, inspect.isclass)
               if issubclass(c, Bot) and c is not Bot and c.__module__ == module.__name__]
    if len(classes) != 1:
        raise ValueError("define `bot = MyBot()` or exactly one Bot subclass")
    return classes[0]()
