"""Money: every player's bankroll, and the buying power it gives, the same for people and bots,
live, in lockstep and offline.

Amounts are in tick-lots, and a tick is a cent, so 100 tick-lots are a dollar and a price of
10,000 ticks is $100.00 a lot.

Buying power: a player may hold, counting their open orders as if they all filled, at most as many
lots either way as their equity buys at the order's price, where equity is the balance they
started the round with, plus the cash their trades brought in or paid out, less fees, plus their
position at the middle of the market. A long position is paid for in full and a short one needs
the same amount set aside. In a round with several assets, one bankroll carries them all: the
equity counts every asset's trades, and each asset's buying power is that equity less what the
other assets commit (`committed`). A player whose equity does not cover a single lot is bust:
they can close positions but not open them.
"""

from __future__ import annotations

import math

DOLLAR = 100                  # tick-lots
DEFAULT_BANKROLL = 10_000 * DOLLAR
LOT_PRICE = 10_000            # a lot of the public markets at their reference price: $100


def dollars(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount) / DOLLAR:,.2f}"


def mark(bid: int | None, ask: int | None, last: int | None, reference: int) -> float:
    """What a position is worth a lot: the middle of the market, else the last trade."""
    if bid and ask:
        return (bid + ask) / 2
    return float(last or reference)


def equity(balance: float, cash: float, fees: float, position: int, at: float) -> float:
    return balance + cash - fees + position * at


def max_lots(equity_now: float, price: float) -> int:
    """The most lots, either way, that this equity carries at this price."""
    if price <= 0 or equity_now <= 0:
        return 0
    return math.floor(equity_now / price + 1e-9)


def room(side: str, position: int, open_lots: int, equity_now: float, price: float) -> int:
    """How many more lots may be bid (side "buy") or offered ("sell") at `price`, on top of
    `open_lots` already open on that side."""
    limit = max_lots(equity_now, price)
    if side == "buy":
        return max(0, limit - position - open_lots)
    return max(0, limit + position - open_lots)


def committed(position: int, open_buy: int, open_sell: int, at: float) -> float:
    """What a position in one asset ties up of the bankroll, its open orders counted as if they
    all filled: the larger of the two ways it could end up, at the mark. In a round with several
    assets, each one's buying power is the equity less what the others commit."""
    return max(abs(position + open_buy), abs(position - open_sell)) * at
