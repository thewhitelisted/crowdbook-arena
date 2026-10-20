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

from .bot import Bot, Fill, News, Quote, State, Trade
from .tracker import SeatTracker

log = logging.getLogger(__name__)
SECOND = 1_000_000_000
ARENA_IDS = 1_000_000_000  # the server numbers orders sent without an id from here


def round_start_info(rnd: dict[str, Any] | None) -> dict[str, Any]:
    """What a bot's on_start gets about a round from the server's description of it: the round,
    its market, length and briefing, and when headlines come if it has news."""
    if not rnd:
        return {}
    config = rnd.get("config") or {}
    info = {"round": rnd.get("id"), "market": rnd.get("market"),
            "duration_s": config.get("duration_s"), "briefing": config.get("briefing", "")}
    if "news" in config:
        info["news"] = config["news"]
    return info


def ws_url(server: str) -> str:
    parts = urlsplit(server if "://" in server else f"http://{server}")
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[parts.scheme]
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/ws", "", ""))


class LiveDriver:
    """Turns a bot's quotes into orders in one live round."""

    def __init__(self, bot: Bot, tracker: SeatTracker, send: Callable[[dict[str, Any]], Any],
                 interval: int = SECOND, info: dict[str, Any] | None = None):
        self.bot = bot
        self.t = tracker
        self.send = send
        self.info = info or {}  # what on_start gets about the round, besides the seat's limits
        self.interval = interval
        self.next_turn: int | None = None
        self.trades: list[Trade] = []
        self.fills: list[Fill] = []
        self.news: list[News] = []
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
            self.bot.on_start({**self.info, "mode": "live",
                               "max_position": self.t.max_position,
                               "max_order_quantity": self.t.max_order_quantity,
                               "duration_ns": self.t.duration})
        if kind == "filled" or self.next_turn is None or self.t.time >= self.next_turn:
            self.next_turn = (self.t.time // self.interval + 1) * self.interval
            await self.turn()

    async def turn(self) -> None:
        state = self.t.state(self.trades, self.fills)
        state.news = self.news
        self.trades, self.fills, self.news = [], [], []
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

    def _room(self, side: str, price: float) -> int:
        """Lots one order on this side may have: within the position limit, the largest order,
        and the seat's buying power, its other order on the side being replaced."""
        t = self.t
        room = t.max_position - t.position if side == "buy" else t.max_position + t.position
        afford = self.t.state().room(side, price)
        return max(0, min(room, t.max_order_quantity, afford))

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
            price = q.take_price
            if price is None:
                other = self.t.asks if taken == "buy" else self.t.bids
                price = other[0][0] if other else None
            qty = min(abs(q.take), self._room(taken, price)) if price is not None else 0
            if qty > 0:
                await self._send({"type": "new", "id": self._new_id(), "side": taken,
                                  "order_type": "limit", "time_in_force": "immediate-or-cancel",
                                  "price": price, "quantity": qty})
        for side, want in (("buy", q.bid), ("sell", q.ask)):
            if side == taken:
                continue
            if any(p["side"] == side for p in self.t.pending.values()):
                continue  # an order on this side is still on its way: wait for its answer
            resting = [o for o in self.t.my_side(side) if o["id"] not in self.cancelling]
            size = min(want[1], self._room(side, want[0])) if want else 0
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


class Session:
    """A bot's side of its connection to an arena: what it does with each message from the
    server, whatever carries the messages (a WebSocket here, a browser tab's in the browser).
    `send` takes each message for the server."""

    def __init__(self, bot: Bot, send: Callable[[dict[str, Any]], Any], *,
                 rounds: int | None = None):
        self.bot = bot
        self.send = send
        self.rounds = rounds
        self.done: list[dict[str, Any]] = []
        self.me: dict[str, Any] | None = None
        self.tracker: SeatTracker | None = None
        self.driver: LiveDriver | None = None
        self.live_round: int | None = None
        self.known: dict[int, dict[str, Any]] = {}  # the rounds the server has described, by id

    async def handle(self, msg: dict[str, Any]) -> bool:
        """Acts on one message from the server; True once `rounds` rounds have ended. Raises
        ConnectionError when the server refuses the connection."""
        bot, send = self.bot, self.send
        kind = msg.get("type")
        if kind in ("welcome", "game") and msg["game"].get("current"):
            self.known[msg["game"]["current"]["id"]] = msg["game"]["current"]
        elif kind == "round_started":
            self.known[msg["round"]["id"]] = msg["round"]
        if kind == "welcome":
            self.me = msg["player"]
        elif kind == "error":
            if msg.get("fatal"):
                raise ConnectionError(msg.get("message"))
            log.warning("server: %s", msg.get("message"))
            request = msg.get("request")
            if self.tracker and isinstance(request, dict) and "id" in request:
                # The server refused it, so the gateway never saw it.
                self.tracker.pending.pop(request["id"], None)
        elif kind == "live_snapshot" and msg.get("seated"):
            if (self.driver is not None and self.tracker is not None
                    and self.live_round == msg["round"]):
                self.tracker.load(msg["state"])  # the same round again: carry on as we were
                self.driver.cancelling.clear()
                self.driver.modifying.clear()
            else:
                self.tracker = SeatTracker()
                self.tracker.load(msg["state"])
                self.driver = LiveDriver(bot, self.tracker, send,
                                         info=round_start_info(self.known.get(msg["round"])))
                self.live_round = msg["round"]
        elif kind == "news" and self.driver and msg["round"] == self.live_round:
            self.driver.news.append(News(msg["time"], msg["headline"], msg["change"]))
        elif (kind == "live" and self.tracker and self.driver
              and msg["round"] == self.live_round):
            self.tracker.apply(msg["m"])
            await self.driver.on_message(msg["m"])
        elif kind == "turn":
            # A lockstep round: answer every turn with this turn's quote.
            if msg["round"] != self.live_round:
                self.live_round = msg["round"]
                s = msg["state"]
                try:
                    bot.on_start({**round_start_info(self.known.get(msg["round"])),
                                  "mode": "lockstep", "round": msg["round"],
                                  "max_position": s["max_position"],
                                  "max_order_quantity": s["max_order_quantity"]})
                except Exception:
                    log.exception("the bot's on_start raised")
            try:
                quote = bot.on_turn(State.from_json(msg["state"]))
            except Exception:
                log.exception("the bot's on_turn raised; it answers nothing this turn")
                quote = None
            answer = quote.to_json() if quote is not None else _keep(msg["state"])
            await send({"type": "orders", "turn": msg["turn"], **answer})
        elif kind == "round_ended":
            me = self.me
            mine = next((r for r in msg["results"] if me and r["player_id"] == me["id"]), None)
            result = {"round": msg["round"], "result": mine, "results": msg["results"]}
            if mine is not None:
                bot.on_end(result)
            self.done.append(result)
            self.known.pop(msg["round"]["id"], None)
            self.tracker = self.driver = None
            self.live_round = None
            if self.rounds is not None and len(self.done) >= self.rounds:
                return True
        return False


async def play(bot: Bot, server: str, token: str, *, rounds: int | None = None,
               on_event: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """Plays `bot` from the seat of the player whose token this is, in every round the host
    starts, until `rounds` have ended (every round until the connection closes if None). Returns
    each round's results."""
    import websockets

    async with websockets.connect(ws_url(server), max_size=None) as ws:
        async def send(msg: dict[str, Any]) -> None:
            await ws.send(json.dumps(msg))

        session = Session(bot, send, rounds=rounds)
        await send({"type": "auth", "token": token})
        async for raw in ws:
            msg = json.loads(raw)
            if on_event:
                on_event(msg)
            if await session.handle(msg):
                break
        return session.done


def _keep(state: dict[str, Any]) -> dict[str, Any]:
    """The answer that leaves the seat's quotes as they are."""
    return {"bid": state.get("my_bid"), "ask": state.get("my_ask"), "take": 0,
            "take_price": None}


def run(bot: Bot, server: str, token: str, rounds: int | None = None) -> list[dict[str, Any]]:
    return asyncio.run(play(bot, server, token, rounds=rounds))
