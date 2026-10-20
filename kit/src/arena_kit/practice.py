"""Practice data: recorded runs of the public market kinds, to study before writing a bot.

A recording is the market as it would run for a seed with no player in it, seen every quarter
second: the best bid and offer, the mid, the trades, and the true value, which no player sees in
a round. Markets with news have their headlines too. The same seed always gives the same
recording.

    arena-kit practice --market news --seeds 1-5 --out data/
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .bot import News, Trade
from .markets import (SECOND, MarketKind, briefing_of, has_news, headlines, is_linked,
                      public_kind)
from .stepped import SteppedMarket

STEP = "250ms"


@dataclass(frozen=True)
class Tick:
    time: int                 # simulated nanoseconds since the start
    bid: int | None
    ask: int | None
    mid: float | None
    last_trade: int | None
    volume: int               # lots traded since the last tick
    value: float              # the true value


@dataclass
class Recording:
    market: str
    seed: int
    duration_s: int
    news: str
    briefing: str
    asset: str | None = None  # in a round with several assets, the one this records
    ticks: list[Tick] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    headlines: list[News] = field(default_factory=list)

    def write(self, out: str | Path) -> list[Path]:
        """Writes the recording as CSV files (and its briefing as text) in `out`, named after
        the market and the seed; returns their paths."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{self.market}-{self.seed}" + (f"-{self.asset}" if self.asset else "")
        paths = [out / f"{stem}-book.csv", out / f"{stem}-trades.csv"]
        _csv(paths[0], ["time_s", "bid", "ask", "mid", "last_trade", "volume", "value"],
             ([_s(t.time), t.bid, t.ask, t.mid, t.last_trade, t.volume, f"{t.value:.3f}"]
              for t in self.ticks))
        _csv(paths[1], ["time_s", "price", "quantity", "aggressor"],
             ([_s(t.time), t.price, t.quantity, t.aggressor] for t in self.trades))
        if self.headlines or (not self.asset and has_news(public_kind(self.market))):
            paths.append(out / f"{stem}-headlines.csv")
            _csv(paths[-1], ["time_s", "headline", "change"],
                 ([_s(n.time), n.headline, n.change] for n in self.headlines))
        paths.append(out / f"{stem}-briefing.txt")
        paths[-1].write_text(self.briefing + "\n")
        return paths


def _s(ns: int) -> str:
    return f"{ns / SECOND:.6f}"


def _csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(["" if x is None else x for x in row] for row in rows)


def record(market: str, seed: int = 1, duration_s: int = 300,
           news: str = "experts") -> Recording:
    """Records a public market kind for `seed`, with headlines `news` late (as in
    NEWS_DELAYS). Only public kinds: a server's own kinds stay on the server. For a kind with
    several assets, use `record_all`."""
    kind: MarketKind = public_kind(market)
    if is_linked(kind):
        raise ValueError(f"{market} has several assets: record_all() records each one")
    return _record(kind, kind.scenario(seed, duration_s), seed, duration_s, news, None)


def record_all(market: str, seed: int = 1, duration_s: int = 300,
               news: str = "experts") -> list[Recording]:
    """Every asset of a public kind for `seed`: one recording, or one for each linked asset,
    all from the same round."""
    kind: MarketKind = public_kind(market)
    if not is_linked(kind):
        return [record(market, seed, duration_s, news)]
    return [_record(kind, text, seed, duration_s, news, sym)
            for sym, text in kind.scenarios(seed, duration_s).items()]


def _record(kind: MarketKind, scenario: str, seed: int, duration_s: int, news: str,
            asset: str | None) -> Recording:
    # Decisions every quarter second instead of every second; the seat never trades.
    if 'interval = "1s"' not in scenario:
        raise ValueError(f"market kind {kind.name!r} cannot be recorded")
    m = SteppedMarket(scenario.replace('interval = "1s"', f'interval = "{STEP}"', 1), seed)
    linked = asset is not None
    rec = Recording(market=kind.name, seed=int(seed), duration_s=int(duration_s), news=news,
                    briefing=briefing_of(kind, seed, duration_s, None if linked else news),
                    asset=asset,
                    headlines=[] if linked else headlines(kind, seed, duration_s, news))
    while True:
        s = m.state()
        rec.trades.extend(s.trades)
        bid = s.bid[0] if s.bid else None
        ask = s.ask[0] if s.ask else None
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        rec.ticks.append(Tick(s.time, bid, ask, mid, s.last_trade,
                              sum(t.quantity for t in s.trades), m.value))
        if m.done:
            return rec
        m.step(None)
