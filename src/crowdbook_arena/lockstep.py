"""Lockstep rounds: every bot trades its own copy of the same market, from the same seed, and the
market waits for the bot at every turn.

At each decision (every simulated second, and whenever one of the bot's orders fills) the server
sends the bot its state:

    {"type": "turn", "round": R, "turn": N, "state": {...}}

and the bot answers within the round's turn timeout:

    {"type": "orders", "turn": N, "bid": [price, size] | null, "ask": ..., "take": 0}

What the bot answers reaches the market at the turn's simulated time, so how long it took to think
or how far away it is changes nothing, as long as it answers in time. A late answer, or none, or a
bot with no connection, leaves its quotes as they were and takes nothing. Every bot meets the same
market, so the same answers always give the same score.

The copies share one clock. Every copy is the same market, so a bot that ran ahead could tell
another what is coming. So no seat is shown a moment of its market from simulated second k on until
every seat has finished every decision before second k: the seats move through the round one
simulated second at a time, together.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from arena_kit.bot import News, Quote
from arena_kit.money import DEFAULT_BANKROLL
from arena_kit.stepped import SteppedMarket

log = logging.getLogger(__name__)

Send = Callable[[int, dict[str, Any]], Awaitable[None]]
SECOND = 1_000_000_000
RECONNECT_S = 5.0  # how long a bot that has just gone is waited for, at most


@dataclass
class LockstepSeat:
    player_id: int
    market: SteppedMarket
    turn: int = 0
    timeouts: int = 0       # turns it did not answer in time
    absent: int = 0         # turns it had no connection for
    invalid: int = 0        # answers that were not a valid quote
    answer: asyncio.Future | None = None
    waiting: bool = False   # at the shared clock, for the others to catch up
    done: bool = False
    was_absent: bool = False
    back: asyncio.Event = field(default_factory=asyncio.Event)  # set when it reconnects
    task: asyncio.Task | None = field(default=None, repr=False)


class LockstepRound:
    def __init__(self, *, round_id: int, scenario: str, seed: int, player_ids: list[int],
                 timeout_ms: int, send: Send, connected: Callable[[int], bool],
                 news: list[News] | tuple = (),
                 balances: dict[int, float] | None = None,
                 on_progress: Callable[[dict[str, Any]], Awaitable[None]],
                 on_end: Callable[[dict[int, tuple[float, dict[str, Any]]]], Awaitable[None]]):
        self.round_id = round_id
        self.seed = seed
        self.timeout = timeout_ms / 1000
        self.seats = {pid: LockstepSeat(pid, SteppedMarket(
                          scenario, seed, news, (balances or {}).get(pid, DEFAULT_BANKROLL)))
                      for pid in player_ids}
        self.duration = max(s.market.state().time_left + s.market.state().time
                            for s in self.seats.values())
        self.second = 1        # seats may be shown moments before this simulated second
        self._tick = asyncio.Event()
        self._send = send
        self._connected = connected
        self._on_progress = on_progress
        self._on_end = on_end
        self._stopping = False
        self._aborted = False
        self._finished = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        for seat in self.seats.values():
            seat.task = asyncio.create_task(self._run(seat))
        self._tasks = [asyncio.create_task(self._progress()), asyncio.create_task(self._wait_all())]

    # The shared clock

    def _advance(self) -> None:
        """Moves the clock on a second once every seat still trading is waiting at it."""
        if all(s.done or s.waiting for s in self.seats.values()):
            self.second += 1
            for s in self.seats.values():
                s.waiting = False
            tick, self._tick = self._tick, asyncio.Event()
            tick.set()

    async def _catch_up(self, seat: LockstepSeat, time: int) -> None:
        while time >= self.second * SECOND and not self._stopping:
            seat.waiting = True
            tick = self._tick
            self._advance()
            await tick.wait()

    async def _run(self, seat: LockstepSeat) -> None:
        m = seat.market
        try:
            while not m.done and not self._stopping:
                state = m.state()
                await self._catch_up(seat, state.time)
                if self._stopping:
                    break
                seat.turn += 1
                quote = await self._ask(seat, state.to_json())
                m.step(quote)
        except Exception:
            log.exception("lockstep round %s seat %s failed", self.round_id, seat.player_id)
        finally:
            seat.done = True
            self._advance()

    async def _ask(self, seat: LockstepSeat, state: dict[str, Any]) -> Quote | None:
        if not self._connected(seat.player_id):
            if not seat.was_absent:
                # Just gone: give it a little while to come back before standing it aside.
                seat.was_absent = True
                seat.back.clear()
                try:
                    await asyncio.wait_for(seat.back.wait(), min(self.timeout, RECONNECT_S))
                except asyncio.TimeoutError:
                    pass
            if not self._connected(seat.player_id):
                seat.absent += 1
                return None
        seat.was_absent = False
        loop = asyncio.get_running_loop()
        seat.answer = loop.create_future()
        await self._send(seat.player_id, {"type": "turn", "round": self.round_id,
                                          "turn": seat.turn, "state": state})
        try:
            answer = await asyncio.wait_for(seat.answer, self.timeout)
        except asyncio.TimeoutError:
            seat.timeouts += 1
            return None
        finally:
            seat.answer = None
        try:
            return Quote.from_json(answer)
        except ValueError:
            seat.invalid += 1
            return None

    def answer(self, player_id: int, msg: dict[str, Any]) -> str | None:
        """A bot's answer to its turn; returns why it was not taken, or None."""
        seat = self.seats.get(player_id)
        if seat is None:
            return "you have no seat in this round"
        if seat.answer is None or seat.answer.done() or msg.get("turn") != seat.turn:
            return "not your turn: the answer was late, or for another turn"
        seat.answer.set_result(msg)
        return None

    def disconnected(self, player_id: int) -> None:
        """The player's last connection went: stop waiting for this turn's answer."""
        seat = self.seats.get(player_id)
        if seat and seat.answer is not None and not seat.answer.done():
            seat.answer.set_exception(asyncio.TimeoutError())

    def reconnected(self, player_id: int) -> None:
        seat = self.seats.get(player_id)
        if seat:
            seat.back.set()

    def progress(self) -> dict[str, Any]:
        """How far the round is, in simulated time, and which bots have finished: never one
        seat's own turn, which would tell the others how its market went."""
        return {"time": min(self.second * SECOND, self.duration), "duration": self.duration,
                "done": [pid for pid, s in self.seats.items() if s.done]}

    async def _progress(self) -> None:
        while not self._finished.is_set():
            await self._on_progress(self.progress())
            await asyncio.sleep(1)

    async def _wait_all(self) -> None:
        scores: dict[int, tuple[float, dict[str, Any]]] = {}
        try:
            await asyncio.gather(*(s.task for s in self.seats.values() if s.task))
            for pid, seat in self.seats.items():
                try:
                    s = seat.market.state()
                    scores[pid] = (seat.market.score, {
                        "ended": seat.market.done, "turns": seat.turn,
                        "timeouts": seat.timeouts, "absent": seat.absent,
                        "invalid": seat.invalid, "position": s.position, "fees": s.fees,
                        "seed": self.seed})
                except Exception:
                    log.exception("lockstep round %s: no score for seat %s", self.round_id, pid)
                    scores[pid] = (0.0, {"ended": False, "error": True})
        finally:
            self._finished.set()
            for t in self._tasks:
                if t is not asyncio.current_task():
                    t.cancel()
        if not self._aborted:
            await self._on_end(scores)

    async def stop(self) -> None:
        """Ends the round early: each market is scored where it stands."""
        self._stopping = True
        for seat in self.seats.values():
            if seat.answer is not None and not seat.answer.done():
                seat.answer.set_exception(asyncio.TimeoutError())
            seat.back.set()
        tick, self._tick = self._tick, asyncio.Event()
        tick.set()
        await self._finished.wait()

    async def abort(self) -> None:
        """The server is stopping: end without results; the round is recorded as failed."""
        self._aborted = True
        await self.stop()

    async def wait(self) -> None:
        await self._finished.wait()
