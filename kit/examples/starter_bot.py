"""A starter bot: a careful market maker. It quotes ten lots a side around the market's fair
price as it sees it (the middle, weighted toward the side with less queued, leaned a little with
the recent buying or selling), keeps its quotes a tick and a half away so the well-informed do
not pick them off too often, and leans them against what it holds. When news breaks it stops
quoting for a few seconds and holds a position the way the news points instead. It gets flat
before the close.

    arena-kit test kit/examples/starter_bot.py                     # offline, in a local market
    arena-kit join kit/examples/starter_bot.py --server http://localhost:8000 --code ABCDEF
"""

import math

from arena_kit import Bot, Quote, State

SIZE = 10           # lots a side
HALF = 1.5          # ticks from the fair price to each quote
FLOW_WEIGHT = 0.3   # ticks of lean for each lot of recent net buying
FLOW_SMOOTH = 0.3   # how quickly the remembered flow follows the latest turn
INVENTORY = 0.005   # ticks of lean for each lot held
QUIET_TURNS = 5     # turns to trade the news instead of quoting, after a headline
NEWS_LOTS = 2       # lots to hold for each tick the headline says the value moved, at most 40
FLATTEN_S = 10      # seconds before the close to take the position off


class StarterBot(Bot):
    def __init__(self):
        self.flow = 0.0
        self.quiet = 0
        self.target = 0

    def on_turn(self, state: State) -> Quote | None:
        net = sum(t.quantity if t.aggressor == "buy" else -t.quantity for t in state.trades)
        self.flow = (1 - FLOW_SMOOTH) * self.flow + FLOW_SMOOTH * net
        for n in state.news:
            # News: the price is about to move. Get out of its way, and go with it.
            self.quiet = QUIET_TURNS
            self.target = max(-40, min(40, NEWS_LOTS * n.change))
        if state.bid is None or state.ask is None:
            return Quote()  # no market to quote around: stand aside
        if state.time_left < FLATTEN_S * 1_000_000_000:
            return Quote(take=-state.position)  # nearly over: cancel the quotes and get flat
        if self.quiet:
            self.quiet -= 1
            want = self.target if self.quiet else 0  # then back to flat, and to quoting
            return Quote(take=want - state.position)
        (bid, bid_size), (ask, ask_size) = state.bid, state.ask
        micro = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
        fair = micro + FLOW_WEIGHT * self.flow - INVENTORY * state.position
        my_bid = min(math.floor(fair - HALF), ask - 1)
        my_ask = max(math.ceil(fair + HALF), bid + 1)
        buy = max(0, min(SIZE, state.max_position - state.position))
        sell = max(0, min(SIZE, state.max_position + state.position))
        return Quote(bid=(my_bid, buy) if buy else None, ask=(my_ask, sell) if sell else None)


bot = StarterBot()
