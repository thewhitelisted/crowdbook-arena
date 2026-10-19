"""A starter bot: quotes a lot or two either side of the middle of the market, leans its quotes
against its position so it does not pile up inventory, and gets flat in the last ten seconds.

    arena-kit test kit/examples/starter_bot.py                     # offline, in a local market
    arena-kit join kit/examples/starter_bot.py --server http://localhost:8000 --code ABCDEF
"""

from arena_kit import Bot, Quote, State

SIZE = 2


class StarterBot(Bot):
    def on_turn(self, state: State) -> Quote | None:
        if state.bid is None or state.ask is None:
            return Quote()  # no market to quote around: stand aside
        if state.time_left < 10_000_000_000:
            # Ten seconds to go: cancel the quotes and take the position off.
            return Quote(take=-state.position)
        mid = (state.bid[0] + state.ask[0]) / 2
        lean = round(state.position / 10)  # a tick for every ten lots held
        bid = min(int(mid - 1) - lean, state.ask[0] - 1)
        ask = max(int(mid + 1.5) - lean, state.bid[0] + 1)
        room = state.max_position - abs(state.position)
        size = max(1, min(SIZE, room))
        return Quote(bid=(bid, size), ask=(ask, size))


bot = StarterBot()
