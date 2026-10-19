"""crowdbook-arena's kit: write a trading bot once, test it offline against a local crowdbook
market, and run the same code in a game on an arena server."""

from .bot import Bot, Fill, Quote, State, Trade
from .markets import PUBLIC_KINDS, MarketKind
from .stepped import LocalResult, SteppedMarket, run_local

__all__ = ["Bot", "Fill", "LocalResult", "MarketKind", "PUBLIC_KINDS", "Quote", "State",
           "SteppedMarket", "Trade", "run_local"]
