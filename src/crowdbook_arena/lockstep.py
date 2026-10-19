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
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from arena_kit.bot import Quote
from arena_kit.stepped import SteppedMarket

log = logging.getLogger(__name__)

Send = Callable[[int, dict[str, Any]], Awaitable[None]]


@dataclass
class LockstepSeat:
    player_id: int
    market: SteppedMarket
    turn: int = 0
    timeouts: int = 0       # turns it did not answer in time
    absent: int = 0         # turns it had no connection for
    invalid: int = 0        # answers that were not a valid quote
    answer: asyncio.Future | None = None
    done: bool = False
    task: asyncio.Task | None = field(default=None, repr=False)


class LockstepRound:
    def __init__(self, *, round_id: int, scenario: str, seed: int, player_ids: list[int],
                 timeout_ms: int, send: Send, connected: Callable[[int], bool],
                 on_progress: Callable[[dict[int, int]], Awaitable[None]],
                 on_end: Callable[[dict[int, tuple[float, dict[str, Any]]]], Awaitable[None]]):
        self.round_id = round_id
        self.seed = seed
        self.timeout = timeout_ms / 1000
        self.seats = {pid: LockstepSeat(pid, SteppedMarket(scenario, seed)) for pid in player_ids}
        self._send = send
        self._connected = connected
        self._on_progress = on_progress
        self._on_end = on_end
        self._stopping = False
        self._finished = asyncio.Event()
        self._progress_task: asyncio.Task | None = None

    async def start(self) -> None:
        for seat in self.seats.values():
            seat.task = asyncio.create_task(self._run(seat))
        self._progress_task = asyncio.create_task(self._progress())
        asyncio.create_task(self._wait_all())

    async def _run(self, seat: LockstepSeat) -> None:
        m = seat.market
        try:
            while not m.done and not self._stopping:
                state = m.state()
                seat.turn += 1
                quote = await self._ask(seat, state.to_json())
                m.step(quote)
                await asyncio.sleep(0)  # let the other seats and connections run
        except Exception:
            log.exception("lockstep round %s seat %s failed", self.round_id, seat.player_id)
        finally:
            seat.done = True

    async def _ask(self, seat: LockstepSeat, state: dict[str, Any]) -> Quote | None:
        if not self._connected(seat.player_id):
            seat.absent += 1
            return None
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

    def progress(self) -> dict[int, int]:
        return {pid: s.turn for pid, s in self.seats.items()}

    async def _progress(self) -> None:
        while not self._finished.is_set():
            await self._on_progress(self.progress())
            await asyncio.sleep(1)

    async def _wait_all(self) -> None:
        await asyncio.gather(*(s.task for s in self.seats.values() if s.task))
        scores = {}
        for pid, seat in self.seats.items():
            s = seat.market.state()
            scores[pid] = (seat.market.score, {
                "ended": seat.market.done, "turns": seat.turn, "timeouts": seat.timeouts,
                "absent": seat.absent, "invalid": seat.invalid, "position": s.position,
                "fees": s.fees, "seed": self.seed})
        self._finished.set()
        if self._progress_task:
            self._progress_task.cancel()
        await self._on_end(scores)

    async def stop(self) -> None:
        """Ends the round early: each market is scored where it stands."""
        self._stopping = True
        for seat in self.seats.values():
            if seat.answer is not None and not seat.answer.done():
                seat.answer.set_exception(asyncio.TimeoutError())
        await self._finished.wait()

    async def wait(self) -> None:
        await self._finished.wait()
