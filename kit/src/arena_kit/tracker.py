"""A seat's view of a live market, kept up to date from crowdbook's gateway messages
(crowdbook's docs/protocol.md): its account, its open orders, the book and the tape. The server
keeps one for each seat, to show a player who reconnects where they are, and a bot keeps its own.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .bot import Fill, State, Trade

FEE_UNITS = 1000  # fees and scores are sent in thousandths of a tick-lot


class SeatTracker:
    def __init__(self, keep_trades: int = 100):
        self.time = 0
        self.duration = 0
        self.started = False
        self.ended: dict[str, Any] | None = None
        self.reference_price = 0
        self.max_position = 0
        self.max_order_quantity = 0
        self.maker_fee = 0.0
        self.taker_fee = 0.0
        self.cash = 0.0       # tick-lots, change since the start
        self.position = 0
        self.fees = 0.0       # tick-lots
        self.traded = 0       # lots bought and sold
        self.orders: dict[int, dict[str, Any]] = {}  # live orders the exchange has accepted
        self.pending: dict[int, dict[str, Any]] = {}  # new orders sent and not yet answered
        self.bids: list[tuple[int, int]] = []
        self.asks: list[tuple[int, int]] = []
        self.last_trade: int | None = None
        self.trades: deque[Trade] = deque(maxlen=keep_trades)
        self.fills: deque[Fill] = deque(maxlen=keep_trades)
        self._initial_cash = 0
        self._initial_position = 0

    # The client's own requests, so that orders in flight are known before the exchange answers.
    def sent(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "new":
            self.pending[msg["id"]] = dict(msg)

    def live_ids(self) -> list[int]:
        """Every order that may still rest or trade: accepted and open, or sent and unanswered."""
        return sorted(set(self.orders) | set(self.pending))

    def apply(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        if "time" in m and isinstance(m["time"], int):
            self.time = max(self.time, m["time"])
        if kind == "welcome":
            self.duration = m["duration"]
            self.started = m["started"]
            self.reference_price = m["reference_price"]
            self.maker_fee = m["maker_fee"] / FEE_UNITS
            self.taker_fee = m["taker_fee"] / FEE_UNITS
            acct = m["account"]
            self._initial_cash = acct["initial_cash"]
            self._initial_position = acct["initial_position"]
            self.cash = acct["cash"] - acct["initial_cash"]
            self.position = acct["position"]
            self.fees = acct["fees"] / FEE_UNITS
            self.max_position = acct["max_position"]
            self.max_order_quantity = acct["max_order_quantity"]
        elif kind == "start":
            self.started = True
        elif kind == "accepted":
            self.pending.pop(m["id"], None)
            self.orders[m["id"]] = {"id": m["id"], "side": m["side"],
                                    "order_type": m["order_type"], "price": m.get("price"),
                                    "leaves": m["quantity"]}
        elif kind == "rejected":
            if m["request"] == "new":
                self.pending.pop(m["id"], None)
        elif kind == "modified":
            if m["id"] in self.orders:
                self.orders[m["id"]].update(price=m["price"], leaves=m["quantity"])
        elif kind == "filled":
            qty, price = m["quantity"], m["price"]
            sign = 1 if m["side"] == "buy" else -1
            self.position += sign * qty
            self.cash -= sign * qty * price
            self.fees += m["fee"] / FEE_UNITS
            self.traded += qty
            self.fills.append(Fill(m["time"], m["side"], price, qty, m["fee"] / FEE_UNITS))
            self.pending.pop(m["id"], None)
            if m["leaves"] > 0:
                if m["id"] in self.orders:
                    self.orders[m["id"]]["leaves"] = m["leaves"]
            else:
                self.orders.pop(m["id"], None)
        elif kind == "cancelled":
            self.pending.pop(m["id"], None)
            self.orders.pop(m["id"], None)
        elif kind == "trade":
            self.last_trade = m["price"]
            self.trades.append(Trade(m["time"], m["price"], m["quantity"], m["aggressor"]))
        elif kind == "top":
            # Only what depth does not already say: keep the deeper book when it agrees.
            bid, ask = m.get("bid"), m.get("ask")
            self.bids = _merge_top(self.bids, bid)
            self.asks = _merge_top(self.asks, ask)
        elif kind == "depth":
            self.bids = [(lv["price"], lv["quantity"]) for lv in m["bids"]]
            self.asks = [(lv["price"], lv["quantity"]) for lv in m["asks"]]
        elif kind == "end":
            self.ended = dict(m)
            self.cash = m["cash"] - self._initial_cash
            self.position = m["position"]
            self.fees = m["fees"] / FEE_UNITS
            self.orders.clear()
            self.pending.clear()

    def my_side(self, side: str) -> list[dict[str, Any]]:
        return [o for o in self.orders.values() if o["side"] == side and o["leaves"] > 0]

    def state(self, trades: list[Trade] | None = None,
              fills: list[Fill] | None = None) -> State:
        """The seat as a bot's `State`, with the trades and fills given as news since its last
        turn. A side's own order is its first resting one."""
        mb, ma = self.my_side("buy"), self.my_side("sell")
        return State(
            time=self.time, time_left=max(0, self.duration - self.time),
            position=self.position, cash=self.cash, fees=self.fees,
            bid=self.bids[0] if self.bids else None, ask=self.asks[0] if self.asks else None,
            last_trade=self.last_trade, bids=list(self.bids), asks=list(self.asks),
            my_bid=(mb[0]["price"], mb[0]["leaves"]) if mb else None,
            my_ask=(ma[0]["price"], ma[0]["leaves"]) if ma else None,
            max_position=self.max_position, max_order_quantity=self.max_order_quantity,
            reference_price=self.reference_price, trades=trades or [], fills=fills or [])

    def snapshot(self) -> dict[str, Any]:
        """Everything a screen needs to draw the seat from scratch, as JSON."""
        return {
            "time": self.time, "duration": self.duration, "started": self.started,
            "reference_price": self.reference_price, "max_position": self.max_position,
            "max_order_quantity": self.max_order_quantity, "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee, "cash": self.cash, "position": self.position,
            "fees": self.fees, "traded": self.traded, "orders": list(self.orders.values()),
            "pending": list(self.pending.values()),
            "initial_cash": self._initial_cash, "initial_position": self._initial_position,
            "bids": self.bids, "asks": self.asks, "last_trade": self.last_trade,
            "trades": [t.__dict__ for t in self.trades], "ended": self.ended,
        }

    def load(self, snap: dict[str, Any]) -> None:
        """The reverse of `snapshot`, for a client that joins or rejoins a round under way."""
        self.time = snap["time"]
        self.duration = snap["duration"]
        self.started = snap["started"]
        self.reference_price = snap["reference_price"]
        self.max_position = snap["max_position"]
        self.max_order_quantity = snap["max_order_quantity"]
        self.maker_fee, self.taker_fee = snap["maker_fee"], snap["taker_fee"]
        self.cash, self.position, self.fees = snap["cash"], snap["position"], snap["fees"]
        self.traded = snap.get("traded", 0)
        self.orders = {o["id"]: dict(o) for o in snap["orders"]}
        self.pending = {o["id"]: dict(o) for o in snap.get("pending", [])}
        self._initial_cash = snap.get("initial_cash", 0)
        self._initial_position = snap.get("initial_position", 0)
        self.bids = [tuple(x) for x in snap["bids"]]
        self.asks = [tuple(x) for x in snap["asks"]]
        self.last_trade = snap["last_trade"]
        self.trades.clear()
        self.trades.extend(Trade(**t) for t in snap["trades"])
        self.ended = snap["ended"]


def _merge_top(levels: list[tuple[int, int]], top: dict[str, int] | None) -> list[tuple[int, int]]:
    if top is None:
        return []
    best = (top["price"], top["quantity"])
    if levels and levels[0][0] == best[0]:
        return [best, *levels[1:]]
    return [best]
