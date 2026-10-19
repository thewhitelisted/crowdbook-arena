"""Manual challenges: one-off decisions with exact answers, drawn from a round's seed.

Each challenge has a prompt and the numbers a player needs. It takes an answer, and it values any
answer exactly: the expected profit of a bid, the shells a path of trades ends with, or the
expected log growth of a bet. A score is that value on a scale where doing nothing is 0 and the
best answer is 100, held between -100 and 100, so that challenges of every kind count alike.
Scores and the best answer are revealed only when the round closes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import product
from typing import Any


class AnswerError(ValueError):
    pass


def _number(value: Any, name: str, lo: float, hi: float, whole: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AnswerError(f"{name} must be a number")
    if whole and value != int(value):
        raise AnswerError(f"{name} must be a whole number")
    if not lo <= value <= hi:
        raise AnswerError(f"{name} must be from {lo:g} to {hi:g}")
    return int(value) if whole else float(value)


@dataclass
class Challenge:
    kind: str
    title: str
    prompt: str
    data: dict[str, Any] = field(default_factory=dict)  # what the screen shows besides the prompt

    def check(self, answer: Any) -> dict[str, Any]:
        """The answer, checked and in its canonical form. Raises AnswerError."""
        raise NotImplementedError

    def value(self, answer: dict[str, Any]) -> float:
        raise NotImplementedError

    def nothing(self) -> dict[str, Any]:
        """The answer that does nothing, worth 0 points."""
        raise NotImplementedError

    def best(self) -> dict[str, Any]:
        raise NotImplementedError

    def score(self, answer: dict[str, Any]) -> float:
        base = self.value(self.nothing())
        top = self.value(self.best())
        v = self.value(answer)
        if v == -math.inf:
            return -100.0
        return max(-100.0, min(100.0, 100 * (v - base) / (top - base)))

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "prompt": self.prompt, "data": self.data}

    def solution(self) -> dict[str, Any]:
        best = self.best()
        return {"answer": best, "value": round(self.value(best), 6),
                "nothing": round(self.value(self.nothing()), 6)}


class Auction(Challenge):
    """A first-price sealed-bid auction against simulated rivals with known bid distributions."""

    def __init__(self, seed: int):
        rng = random.Random(f"auction:{seed}")
        self.worth = rng.randrange(120, 241)
        self.rivals = rng.randrange(2, 6)
        self.lo = rng.randrange(int(self.worth * 0.3), int(self.worth * 0.55))
        self.hi = rng.randrange(int(self.worth * 0.9), int(self.worth * 1.3))
        super().__init__(
            "auction", "Sealed bids",
            f"A crate is worth {self.worth} shells to you. You and {self.rivals} rivals each send "
            f"one sealed bid, a whole number of shells. Each rival bids a whole number from "
            f"{self.lo} to {self.hi}, every number as likely as any other, each independently of "
            f"the others. The highest bid wins and pays what it bid; a tie with a rival loses. "
            f"Bid 0 to stay out. What do you bid? You score the profit you expect: "
            f"({self.worth} - your bid) times the chance that you win.",
            {"worth": self.worth, "rivals": self.rivals, "low": self.lo, "high": self.hi})

    def check(self, answer: Any) -> dict[str, Any]:
        if not isinstance(answer, dict) or set(answer) != {"bid"}:
            raise AnswerError('the answer is {"bid": whole number}')
        return {"bid": _number(answer["bid"], "bid", 0, 10 * self.worth, whole=True)}

    def win_chance(self, bid: int) -> float:
        below = min(max(bid - self.lo, 0), self.hi - self.lo + 1)  # rival bids strictly lower
        return (below / (self.hi - self.lo + 1)) ** self.rivals

    def value(self, answer: dict[str, Any]) -> float:
        bid = answer["bid"]
        return 0.0 if bid == 0 else (self.worth - bid) * self.win_chance(bid)

    def nothing(self) -> dict[str, Any]:
        return {"bid": 0}

    def best(self) -> dict[str, Any]:
        bid = max(range(0, self.worth + 1), key=lambda b: (self.value({"bid": b}), -b))
        return {"bid": bid}


class Conversion(Challenge):
    """The best round trip through a table of exchange rates."""

    GOODS = ["Shells", "Pearls", "Coral", "Kelp", "Sand"]
    START = 1000
    MAX_TRADES = 5

    def __init__(self, seed: int):
        rng = random.Random(f"conversion:{seed}")
        n = len(self.GOODS)
        # Each good has a true worth in shells; quoted rates are those worths with noise, so a few
        # round trips gain and most lose.
        worth = [1.0] + [rng.uniform(0.3, 3.0) for _ in range(n - 1)]
        self.rates = [[1.0 if i == j else round(worth[i] / worth[j] * rng.uniform(0.95, 1.04), 4)
                       for j in range(n)] for i in range(n)]
        super().__init__(
            "conversion", "Round trip",
            f"You hold {self.START} Shells. The table gives what one unit of each good (row) "
            f"buys of each other good (column). Make at most {self.MAX_TRADES} trades, each "
            f"turning everything you hold into another good, and end holding Shells. Which "
            f"goods do you pass through? You score the Shells you end with.",
            {"goods": self.GOODS, "rates": self.rates, "start": self.START,
             "max_trades": self.MAX_TRADES})
        if self.value(self.best()) <= self.START + 1e-9:
            # No round trip gains: make Shells to Pearls and back gain 3%.
            self.rates[0][1] = round(1.03 / self.rates[1][0], 4)
            self.data["rates"] = self.rates

    def check(self, answer: Any) -> dict[str, Any]:
        if not isinstance(answer, dict) or set(answer) != {"path"}:
            raise AnswerError('the answer is {"path": [goods, ending with "Shells"]}')
        path = answer["path"]
        if not isinstance(path, list) or len(path) > self.MAX_TRADES:
            raise AnswerError(f"a path is a list of at most {self.MAX_TRADES} goods")
        if any(g not in self.GOODS for g in path):
            raise AnswerError(f"goods are {', '.join(self.GOODS)}")
        if path and path[-1] != "Shells":
            raise AnswerError("the path ends with Shells")
        if any(a == b for a, b in zip(["Shells", *path], path)):
            raise AnswerError("each trade is into a different good")
        return {"path": list(path)}

    def value(self, answer: dict[str, Any]) -> float:
        amount, here = float(self.START), 0
        for good in answer["path"]:
            there = self.GOODS.index(good)
            amount *= self.rates[here][there]
            here = there
        return amount

    def nothing(self) -> dict[str, Any]:
        return {"path": []}

    def best(self) -> dict[str, Any]:
        best, best_value = [], float(self.START)
        n = len(self.GOODS)
        for trades in range(2, self.MAX_TRADES + 1):
            for middle in product(range(n), repeat=trades - 1):
                path = [*middle, 0]
                if any(a == b for a, b in zip([0, *path], path)):
                    continue
                names = [self.GOODS[i] for i in path]
                v = self.value({"path": names})
                if v > best_value + 1e-12:
                    best, best_value = names, v
        return {"path": best}


class Kelly(Challenge):
    """How much to stake on a bet that is played again and again."""

    def __init__(self, seed: int):
        rng = random.Random(f"kelly:{seed}")
        self.p = round(rng.uniform(0.25, 0.75), 2)
        # Net odds with an edge of 4% to 30% of the stake.
        edge = rng.uniform(0.04, 0.3)
        self.odds = round((1 + edge) / self.p - 1, 2)
        if self.p * (1 + self.odds) <= 1.01:
            self.odds = round(1.02 / self.p - 1 + 0.01, 2)
        super().__init__(
            "kelly", "Stake",
            f"A bet wins with probability {self.p:.2f}. Each shell staked returns the stake plus "
            f"{self.odds:.2f} shells when it wins, and is lost when it does not. You will make "
            f"this bet a thousand times, each time staking the same fraction of everything you "
            f"have then. What fraction, from 0 to 1? You score the growth of your wealth you "
            f"expect each bet, p log(1 + f b) + (1 - p) log(1 - f).",
            {"p": self.p, "odds": self.odds})

    def check(self, answer: Any) -> dict[str, Any]:
        if not isinstance(answer, dict) or set(answer) != {"fraction"}:
            raise AnswerError('the answer is {"fraction": a number from 0 to 1}')
        return {"fraction": _number(answer["fraction"], "fraction", 0, 1)}

    def value(self, answer: dict[str, Any]) -> float:
        f = answer["fraction"]
        if f >= 1:
            return -math.inf
        return self.p * math.log1p(f * self.odds) + (1 - self.p) * math.log1p(-f)

    def nothing(self) -> dict[str, Any]:
        return {"fraction": 0.0}

    def best(self) -> dict[str, Any]:
        f = self.p - (1 - self.p) / self.odds
        return {"fraction": round(max(0.0, min(f, 0.999)), 6)}


KINDS = {"auction": Auction, "conversion": Conversion, "kelly": Kelly}
TITLES = {"auction": "Sealed bids", "conversion": "Round trip", "kelly": "Stake"}


def make(kind: str, seed: int) -> Challenge:
    try:
        return KINDS[kind](seed)
    except KeyError:
        raise AnswerError(f"challenges are {', '.join(KINDS)}") from None


class ChallengeRound:
    """A round in which every player answers one challenge before it closes, on the wall clock.
    Answers can be changed until then; scores and the best answer are revealed at the close."""

    def __init__(self, *, round_id: int, challenge: Challenge, player_ids: list[int],
                 duration_s: int, on_end):
        import asyncio
        import time

        self.round_id = round_id
        self.challenge = challenge
        self.seats = {pid: None for pid in player_ids}  # player id -> their answer, if any
        self.duration_s = duration_s
        self.closes_at = time.time() + duration_s
        self._on_end = on_end
        self._closed = False
        self._finished = asyncio.Event()
        self._timer: asyncio.Task | None = None

    async def start(self) -> None:
        import asyncio

        async def close_later() -> None:
            await asyncio.sleep(self.duration_s)
            await self._close()

        self._timer = asyncio.create_task(close_later())

    def public(self, player_id: int) -> dict[str, Any]:
        return {"type": "challenge", "round": self.round_id, "closes_at": self.closes_at,
                **self.challenge.public(), "answer": self.seats.get(player_id)}

    def answer(self, player_id: int, answer: Any) -> dict[str, Any]:
        """Keeps a player's answer, replacing any before it. Raises AnswerError."""
        if self._closed:
            raise AnswerError("the challenge has closed")
        checked = self.challenge.check(answer)
        self.seats[player_id] = checked
        return checked

    async def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        scores: dict[int, tuple[float, dict[str, Any]]] = {}
        for pid, answer in self.seats.items():
            if answer is None:
                scores[pid] = (0.0, {"answered": False})
            else:
                scores[pid] = (round(self.challenge.score(answer), 6), {
                    "answered": True, "answer": answer,
                    "value": round(self.challenge.value(answer), 6)})
        self._finished.set()
        await self._on_end(scores)

    async def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        await self._close()

    async def wait(self) -> None:
        await self._finished.wait()
