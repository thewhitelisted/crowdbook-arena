"""A yardstick: a 40-line market maker that leans its fair price with the smoothed net aggressive
flow. Against the first tuning of the public markets it won 59 rounds in 60, because the
informed traders showed themselves in their market orders; the markets should make following
the flow a skill, not a free lunch (tests/test_yardstick.py)."""

import math
from arena_kit import Bot, Quote, State

SIZE = 20
HALF = 0.5
FLOW_WEIGHT = 0.3
FLOW_SMOOTH = 0.3
INVENTORY = 0.005
UNWIND_S = 20
FLATTEN_S = 3


class MarketMaker(Bot):
    def __init__(self):
        self.flow = 0.0

    def on_turn(self, state: State):
        net = sum(t.quantity if t.aggressor == "buy" else -t.quantity for t in state.trades)
        self.flow = (1 - FLOW_SMOOTH) * self.flow + FLOW_SMOOTH * net
        if state.bid is None or state.ask is None:
            return Quote()
        left = state.time_left / 1e9
        pos = state.position
        if left < FLATTEN_S:
            return Quote(take=-pos)
        (bid, bq), (ask, aq) = state.bid, state.ask
        micro = (bid * aq + ask * bq) / (bq + aq)
        fair = micro + FLOW_WEIGHT * self.flow - INVENTORY * pos
        my_bid = min(math.floor(fair - HALF + 1e-9), ask - 1)
        my_ask = max(math.ceil(fair + HALF - 1e-9), bid + 1)
        size = min(SIZE, state.max_order_quantity)
        buy = max(0, min(size, state.max_position - pos))
        sell = max(0, min(size, state.max_position + pos))
        if left < UNWIND_S:
            if pos >= 0:
                buy = 0
            if pos <= 0:
                sell = 0
        return Quote(bid=(my_bid, buy) if buy else None, ask=(my_ask, sell) if sell else None)
