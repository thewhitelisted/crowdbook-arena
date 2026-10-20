"""Live rounds: a market that runs on the wall clock, served by `crowdbook serve` on the loopback
interface, with a seat for every player.

The arena claims every seat itself, with tokens only it knows, so the clock starts at once and no
one else can trade from a seat. Each seat's gateway messages go to its player's screens and bots;
their requests come back checked, with the seat's own order ids. When a player has no connection
left, their open orders are cancelled, as the gateway does for a connection that closes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from arena_kit.bot import News
from arena_kit.tracker import FEE_UNITS, SeatTracker

from .auth import new_token

log = logging.getLogger(__name__)

Send = Callable[[int, dict[str, Any]], Awaitable[None]]

_FIELDS = {
    "new": {"type", "id", "side", "order_type", "time_in_force", "price", "quantity"},
    "cancel": {"type", "id"},
    "modify": {"type", "id", "price", "quantity"},
    "cancel_all": {"type"},
}
_REQUIRED = {"new": {"side", "order_type", "quantity"}, "cancel": {"id"},
             "modify": {"id", "price", "quantity"}, "cancel_all": set()}
_PUBLIC = {"start", "trade", "top", "depth", "clock", "phase", "indicative"}
_FIRST_ARENA_ID = 1_000_000_000  # ids the arena gives orders sent without one


class RequestError(ValueError):
    pass


def check_request(msg: Any) -> dict[str, Any]:
    """A client's order request, checked field by field so that nothing but new, cancel and
    modify, with their own fields, reaches the gateway."""
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str) or msg["type"] not in _FIELDS:
        raise RequestError("expected new, cancel, modify or cancel_all")
    kind = msg["type"]
    if set(msg) - _FIELDS[kind]:
        raise RequestError(f"unknown fields: {', '.join(sorted(set(msg) - _FIELDS[kind]))}")
    if _REQUIRED[kind] - set(msg):
        raise RequestError(f"missing fields: {', '.join(sorted(_REQUIRED[kind] - set(msg)))}")
    for key in ("id", "price", "quantity"):
        if key in msg:
            v = msg[key]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 2**53:
                raise RequestError(f"{key} must be a positive whole number")
    if kind == "new":
        if msg["side"] not in ("buy", "sell"):
            raise RequestError("side is buy or sell")
        if msg["order_type"] == "limit":
            if "price" not in msg:
                raise RequestError("a limit order needs a price")
            tif = msg.setdefault("time_in_force", "good-till-cancel")
            if tif not in ("good-till-cancel", "immediate-or-cancel", "post-only"):
                raise RequestError("time_in_force is good-till-cancel, immediate-or-cancel "
                                   "or post-only")
        elif msg["order_type"] == "market":
            if "price" in msg or "time_in_force" in msg:
                raise RequestError("a market order has no price or time_in_force")
        else:
            raise RequestError("order_type is limit or market")
    return msg


@dataclass
class Seat:
    player_id: int
    name: str        # the gateway's seat name
    token: str
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    tracker: SeatTracker | None = None
    next_id: int = _FIRST_ARENA_ID
    task: asyncio.Task | None = None


class LiveRound:
    """One live round's gateway process and its seats."""

    def __init__(self, *, round_id: int, scenario: str, seed: int, duration_s: int, speed: float,
                 player_ids: list[int], crowdbook: str, work_dir: str, send: Send,
                 on_end: Callable[[dict[int, tuple[float, dict[str, Any]]]], Awaitable[None]],
                 rate_limit: int = 50, feed_interval_ms: int = 100,
                 watch: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
                 news: list[News] | tuple = (),
                 announce: Callable[[dict[str, Any]], Awaitable[None]] | None = None):
        self.round_id = round_id
        self.seed = seed
        self.duration_s = duration_s
        self.speed = speed
        self.dir = Path(work_dir) / f"round-{round_id}"
        self.scenario = scenario
        self.crowdbook = crowdbook
        self.rate_limit = rate_limit
        self.feed_interval_ms = feed_interval_ms
        self.seats = {pid: Seat(pid, f"p{pid}", new_token()) for pid in player_ids}
        self._send = send
        self._watch_feed = watch  # the market's public messages, for players without a seat
        self._news = sorted(news, key=lambda n: n.time)  # headlines, by when they come out
        self.published: list[dict[str, Any]] = []
        self._announce = announce
        self._on_end = on_end
        self._proc: asyncio.subprocess.Process | None = None
        self._tokens: Path | None = None
        self._finished = asyncio.Event()
        self._ended = False
        self._stdout_task: asyncio.Task | None = None

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            await self._abort()
            raise

    async def _abort(self) -> None:
        for seat in self.seats.values():
            if seat.writer:
                seat.writer.close()
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()

    async def _start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "scenario.toml").write_text(self.scenario)
        tokens = self.dir / "tokens"
        fd = os.open(tokens, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(f"{s.name} {s.token}\n" for s in self.seats.values()))
        self._tokens = tokens
        args = [self.crowdbook, "serve", str(self.dir / "scenario.toml"),
                *[a for s in self.seats.values() for a in ("--seat", s.name)],
                "--listen", "127.0.0.1:0", "--tokens", str(tokens),
                "--seed", str(self.seed), "--duration", f"{self.duration_s}s",
                "--speed", repr(float(self.speed)), "--rate-limit", str(self.rate_limit),
                "--record", str(self.dir / "session.toml"), "--json", str(self.dir / "result.json")]
        if self.feed_interval_ms > 0:
            args += ["--feed-interval", f"{self.feed_interval_ms}ms"]
        self._proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        line = (await asyncio.wait_for(self._proc.stdout.readline(), 10)).decode()
        port = _port(line)
        if port is None:
            self._proc.kill()
            rest = (await asyncio.wait_for(self._proc.stdout.read(), 5)).decode()
            raise RuntimeError(f"crowdbook serve did not start: {line}{rest}".strip())
        self._stdout_task = asyncio.create_task(self._drain_stdout())
        for seat in self.seats.values():
            seat.reader, seat.writer = await asyncio.open_connection(
                "127.0.0.1", port, limit=1 << 20)
            seat.tracker = SeatTracker()
            await self._write(seat, {"type": "hello", "protocol": 1, "seat": seat.name,
                                     "token": seat.token})
            welcome = json.loads(await asyncio.wait_for(seat.reader.readline(), 10) or b"{}")
            if welcome.get("type") != "welcome":
                raise RuntimeError(f"crowdbook serve did not welcome a seat: {welcome}")
            seat.tracker.apply(welcome)
        for seat in self.seats.values():
            seat.task = asyncio.create_task(self._read(seat))

    async def _drain_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        out = await self._proc.stdout.read()
        (self.dir / "serve.log").write_bytes(out)

    async def _write(self, seat: Seat, msg: dict[str, Any]) -> None:
        assert seat.writer
        seat.writer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
        await seat.writer.drain()

    async def _read(self, seat: Seat) -> None:
        assert seat.reader and seat.tracker
        try:
            while line := await seat.reader.readline():
                msg = json.loads(line)
                seat.tracker.apply(msg)
                await self._send(seat.player_id, {"type": "live", "round": self.round_id,
                                                  "m": msg})
                first = seat is next(iter(self.seats.values()))
                if self._watch_feed and first and msg.get("type") in _PUBLIC:
                    await self._watch_feed({"type": "live", "round": self.round_id, "m": msg})
                if first:
                    await self._publish_news(seat.tracker.time)
                if msg.get("type") == "end":
                    break
                if msg.get("type") == "error" and msg.get("fatal"):
                    log.error("round %s seat %s: %s", self.round_id, seat.name, msg)
                    break
        except (ConnectionError, ValueError) as e:
            log.error("round %s seat %s: %s", self.round_id, seat.name, e)
        finally:
            if seat.writer:
                seat.writer.close()
            if all(s.task is None or s.task.done() or s is seat for s in self.seats.values()):
                await self._finish()

    async def _finish(self) -> None:
        if self._ended:
            return
        self._ended = True
        scores: dict[int, tuple[float, dict[str, Any]]] = {}
        for seat in self.seats.values():
            t = seat.tracker
            end = t.ended if t else None
            if end is None:
                # The seat's link failed before the end: value it where it was, at the last
                # trade, so that dropping out never helps.
                if t is None:
                    scores[seat.player_id] = (0.0, {"ended": False})
                    continue
                mark = t.last_trade or t.reference_price
                scores[seat.player_id] = (t.cash + t.position * mark - t.fees, {
                    "ended": False, "position": t.position, "fees": t.fees, "cash": t.cash,
                    "traded": t.traded})
                continue
            if end.get("score"):
                score = end["score"]["total"] / FEE_UNITS
            else:
                score = end["pnl"] - end["fees"] / FEE_UNITS
            scores[seat.player_id] = (score, {
                "ended": True, "pnl": end["pnl"], "fees": end["fees"] / FEE_UNITS,
                "position": end["position"], "cash": t.cash, "traded": t.traded,
                "score": end.get("score")})
        if self._proc and self._proc.returncode is None:
            try:
                await asyncio.wait_for(self._proc.wait(), 10)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._tokens is not None:
            self._tokens.unlink(missing_ok=True)
        self._finished.set()
        await self._on_end(scores)

    async def request(self, player_id: int, msg: dict[str, Any]) -> None:
        """Sends a player's checked request from their seat. Raises RequestError."""
        seat = self.seats.get(player_id)
        if seat is None or seat.writer is None or seat.tracker is None:
            raise RequestError("you have no seat in this round")
        if seat.tracker.ended is not None:
            raise RequestError("the round is over")
        msg = check_request(msg)
        if msg["type"] == "cancel_all":
            await self.cancel_all(player_id)
            return
        if msg["type"] == "new" and "id" not in msg:
            msg["id"] = seat.next_id
            seat.next_id += 1
        seat.tracker.sent(msg)
        try:
            await self._write(seat, msg)
        except ConnectionError as e:
            raise RequestError("the market is closed") from e

    async def cancel_all(self, player_id: int) -> None:
        """Cancels every order of the seat, paced to stay within the gateway's rate limit."""
        seat = self.seats.get(player_id)
        if not (seat and seat.writer and seat.tracker and seat.tracker.ended is None):
            return
        batch = max(1, self.rate_limit // 2)
        for i, oid in enumerate(seat.tracker.live_ids()):
            if i and i % batch == 0:
                await asyncio.sleep(1)
            try:
                await self._write(seat, {"type": "cancel", "id": oid})
            except (ConnectionError, RuntimeError):
                return

    async def _publish_news(self, now: int) -> None:
        """Headlines whose time has come on the market's clock, to everyone in the game."""
        while len(self.published) < len(self._news) and self._news[len(self.published)].time <= now:
            n = self._news[len(self.published)]
            item = {"time": n.time, "headline": n.headline, "change": n.change}
            self.published.append(item)
            if self._announce:
                await self._announce({"type": "news", "round": self.round_id, **item})

    def snapshot(self, player_id: int) -> dict[str, Any] | None:
        seat = self.seats.get(player_id)
        if not (seat and seat.tracker):
            return None
        return {**seat.tracker.snapshot(), "news": list(self.published)}

    def any_snapshot(self) -> dict[str, Any] | None:
        """The market as one seat sees it, for a player without a seat of their own."""
        for seat in self.seats.values():
            if seat.tracker:
                snap = seat.tracker.snapshot()
                return {**snap, "cash": 0, "position": 0, "fees": 0, "traded": 0, "orders": [],
                        "my_fills": [], "news": list(self.published),
                        "pending": [], "initial_cash": 0, "initial_position": 0}
        return None

    async def stop(self) -> None:
        """Ends the round early: the gateway sends every seat its end, as at the closing bell."""
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(self._finished.wait(), 15)
        except asyncio.TimeoutError:
            if self._proc and self._proc.returncode is None:
                self._proc.kill()
            for seat in self.seats.values():
                if seat.task:
                    seat.task.cancel()
            await self._finish()

    async def abort(self) -> None:
        """The server is stopping: kill the market and end without results; the round is
        recorded as failed."""
        self._ended = True  # so that no results are recorded
        await self._abort()
        for seat in self.seats.values():
            if seat.task:
                seat.task.cancel()
        if self._tokens is not None:
            self._tokens.unlink(missing_ok=True)
        self._finished.set()

    async def wait(self) -> None:
        await self._finished.wait()


def _port(line: str) -> int | None:
    import re

    m = re.search(r"serving on [^\s:]+:(\d+)", line)
    return int(m.group(1)) if m else None
