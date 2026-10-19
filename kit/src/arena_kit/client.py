"""Playing a bot in a game on an arena server.

In a live round the market runs on the wall clock and the bot's `Quote`s become orders: the kit
keeps at most one resting order a side, moves it with a modify, and takes with an
immediate-or-cancel order, as a lockstep round or a local test does. Turns come every simulated
second and whenever one of the bot's orders fills.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import websockets

from .bot import Bot, Fill, Quote, State, Trade
from .tracker import SeatTracker

log = logging.getLogger(__name__)
SECOND = 1_000_000_000
ARENA_IDS = 1_000_000_000  # the server numbers orders sent without an id from here


def ws_url(server: str) -> str:
    parts = urlsplit(server if "://" in server else f"http://{server}")
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[parts.scheme]
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/ws", "", ""))


class LiveDriver:
    """Turns a bot's quotes into orders in one live round."""

    def __init__(self, bot: Bot, tracker: SeatTracker, send: Callable[[dict[str, Any]], Any],
                 interval: int = SECOND):
        self.bot = bot
        self.t = tracker
        self.send = send
        self.interval = interval
        self.next_turn: int | None = None
        self.trades: list[Trade] = []
        self.fills: list[Fill] = []
        self.cancelling: set[int] = set()
        self.modifying: dict[int, tuple[int, int]] = {}  # modifies sent and not yet answered
        own = [i for i in (*tracker.orders, *tracker.pending) if i < ARENA_IDS]
        self.next_id = max(own, default=0) + 1
        self.started = False

    async def on_message(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        if kind == "trade":
            self.trades.append(self.t.trades[-1])
        elif kind == "filled":
            self.fills.append(self.t.fills[-1])
        elif kind == "cancelled":
            self.cancelling.discard(m["id"])
            self.modifying.pop(m["id"], None)
        if kind in ("modified", "rejected"):
            self.modifying.pop(m.get("id"), None)
        if not self.t.started or self.t.ended is not None:
            return
        if not self.started:
            self.started = True
            self.bot.on_start({"mode": "live", "max_position": self.t.max_position,
                               "max_order_quantity": self.t.max_order_quantity,
                               "duration_ns": self.t.duration})
        if kind == "filled" or self.next_turn is None or self.t.time >= self.next_turn:
            self.next_turn = (self.t.time // self.interval + 1) * self.interval
            await self.turn()

    async def turn(self) -> None:
        state = self.t.state(self.trades, self.fills)
        self.trades, self.fills = [], []
        try:
            quote = self.bot.on_turn(state)
            quote = quote.checked() if quote is not None else None
        except Exception:
            log.exception("the bot's on_turn raised or gave a bad quote; its orders are left "
                          "as they are")
            return
        if quote is not None:
            await self.place(quote)

    async def _send(self, msg: dict[str, Any]) -> None:
        if msg["type"] == "new":
            self.t.sent(msg)
        await self.send(msg)

    async def _cancel(self, oid: int) -> None:
        if oid not in self.cancelling:
            self.cancelling.add(oid)
            await self._send({"type": "cancel", "id": oid})

    def _room(self, side: str) -> int:
        t = self.t
        room = t.max_position - t.position if side == "buy" else t.max_position + t.position
        return max(0, min(room, t.max_order_quantity))

    def _new_id(self) -> int:
        oid = self.next_id
        self.next_id += 1
        return oid

    async def place(self, q: Quote) -> None:
        taken = None
        if q.take:
            taken = "buy" if q.take > 0 else "sell"
            for o in self.t.my_side(taken):
                await self._cancel(o["id"])
            qty = min(abs(q.take), self._room(taken))
            price = q.take_price
            if price is None:
                other = self.t.asks if taken == "buy" else self.t.bids
                price = other[0][0] if other else None
            if qty > 0 and price is not None:
                await self._send({"type": "new", "id": self._new_id(), "side": taken,
                                  "order_type": "limit", "time_in_force": "immediate-or-cancel",
                                  "price": price, "quantity": qty})
        for side, want in (("buy", q.bid), ("sell", q.ask)):
            if side == taken:
                continue
            if any(p["side"] == side for p in self.t.pending.values()):
                continue  # an order on this side is still on its way: wait for its answer
            resting = [o for o in self.t.my_side(side) if o["id"] not in self.cancelling]
            size = min(want[1], self._room(side)) if want else 0
            if size <= 0:
                for o in resting:
                    await self._cancel(o["id"])
                continue
            price = want[0]
            if not resting:
                await self._send({"type": "new", "id": self._new_id(), "side": side,
                                  "order_type": "limit", "time_in_force": "post-only",
                                  "price": price, "quantity": size})
                continue
            first, *extra = resting
            for o in extra:
                await self._cancel(o["id"])
            now = self.modifying.get(first["id"], (first["price"], first["leaves"]))
            if now != (price, size):
                self.modifying[first["id"]] = (price, size)
                await self._send({"type": "modify", "id": first["id"], "price": price,
                                  "quantity": size})


async def play(bot: Bot, server: str, token: str, *, rounds: int | None = None,
               on_event: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """Plays `bot` from the seat of the player whose token this is, in every round the host
    starts, until `rounds` have ended (every round until the connection closes if None). Returns
    each round's results."""
    done: list[dict[str, Any]] = []
    async with websockets.connect(ws_url(server), max_size=None) as ws:
        async def send(msg: dict[str, Any]) -> None:
            await ws.send(json.dumps(msg))

        await send({"type": "auth", "token": token})
        me: dict[str, Any] | None = None
        tracker: SeatTracker | None = None
        driver: LiveDriver | None = None
        live_round: int | None = None
        async for raw in ws:
            msg = json.loads(raw)
            if on_event:
                on_event(msg)
            kind = msg.get("type")
            if kind == "welcome":
                me = msg["player"]
            elif kind == "error":
                if msg.get("fatal"):
                    raise ConnectionError(msg.get("message"))
                log.warning("server: %s", msg.get("message"))
                request = msg.get("request")
                if tracker and isinstance(request, dict) and "id" in request:
                    # The server refused it, so the gateway never saw it.
                    tracker.pending.pop(request["id"], None)
            elif kind == "live_snapshot" and msg.get("seated"):
                if driver is not None and tracker is not None and live_round == msg["round"]:
                    tracker.load(msg["state"])  # the same round again: carry on as we were
                    driver.cancelling.clear()
                    driver.modifying.clear()
                else:
                    tracker = SeatTracker()
                    tracker.load(msg["state"])
                    driver = LiveDriver(bot, tracker, send)
                    live_round = msg["round"]
            elif kind == "live" and tracker and driver and msg["round"] == live_round:
                tracker.apply(msg["m"])
                await driver.on_message(msg["m"])
            elif kind == "turn":
                # A lockstep round: answer every turn with this turn's quote.
                if msg["round"] != live_round:
                    live_round = msg["round"]
                    bot.on_start({"mode": "lockstep", "round": msg["round"]})
                try:
                    quote = bot.on_turn(State.from_json(msg["state"]))
                except Exception:
                    log.exception("the bot's on_turn raised; it answers nothing this turn")
                    quote = None
                answer = quote.to_json() if quote is not None else _keep(msg["state"])
                await send({"type": "orders", "turn": msg["turn"], **answer})
            elif kind == "round_ended":
                mine = next((r for r in msg["results"] if me and r["player_id"] == me["id"]),
                            None)
                result = {"round": msg["round"], "result": mine, "results": msg["results"]}
                if mine is not None:
                    bot.on_end(result)
                done.append(result)
                tracker = driver = None
                live_round = None
                if rounds is not None and len(done) >= rounds:
                    break
    return done


def _keep(state: dict[str, Any]) -> dict[str, Any]:
    """The answer that leaves the seat's quotes as they are."""
    return {"bid": state.get("my_bid"), "ask": state.get("my_ask"), "take": 0,
            "take_price": None}


def run(bot: Bot, server: str, token: str, rounds: int | None = None) -> list[dict[str, Any]]:
    return asyncio.run(play(bot, server, token, rounds=rounds))
