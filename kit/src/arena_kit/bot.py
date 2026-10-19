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

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask:
            return (self.bid[0] + self.ask[0]) / 2
        return None

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
        return cls(**d)


class Bot:
    """Subclass this and override `on_turn`."""

    def on_start(self, info: dict[str, Any]) -> None:
        """Called once before the first turn, with the round's market and limits."""

    def on_turn(self, state: State) -> Quote | None:
        """The quotes to rest and what to take now; None leaves everything as it is."""
        return None

    def on_end(self, result: dict[str, Any]) -> None:
        """Called once at the end with the round's result for this seat."""
