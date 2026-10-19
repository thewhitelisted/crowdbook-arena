# crowdbook-arena-kit

Write a trading bot once and test it offline against [crowdbook](https://github.com/thewhitelisted/crowdbook)
markets on your own machine. Then play the same code in a
[crowdbook-arena](https://github.com/thewhitelisted/crowdbook-arena) game.

```bash
pip install "crowdbook-arena-kit @ git+https://github.com/thewhitelisted/crowdbook-arena#subdirectory=kit"
```

It needs Python 3.12 or later. On Linux and on macOS on Apple silicon it installs from wheels,
with nothing to compile.

## A bot

```python
from arena_kit import Bot, Quote, State

class MyBot(Bot):
    def on_turn(self, state: State) -> Quote | None:
        if state.bid is None or state.ask is None:
            return Quote()                         # no quotes
        lean = state.position // 10
        return Quote(bid=(state.bid[0] - lean, 2), ask=(state.ask[0] - lean, 2))

bot = MyBot()
```

At each turn the bot gets a `State` and returns a `Quote`.

Turns come every simulated second, and whenever one of the bot's orders fills.

The `State` holds:
- the time and the time left, in nanoseconds;
- position, cash and fees;
- the best bid and offer, the book ten levels deep, and the last trade price;
- the bot's own resting bid and offer;
- the trades and the bot's fills since its last turn;
- its limits.

The `Quote` holds:
- `bid` and `ask`: `(price, size)` to rest in the book, or `None` for nothing on that side. The
  bot keeps at most one order a side. A quote at the price already resting keeps its place in the
  queue, a quote at another price moves the order, and resting quotes never cross the spread.
- `take`: lots to buy now (positive) or sell (negative), immediate-or-cancel, at the best price on
  the other side, or at `take_price`. A take cancels the bot's quote on that side for this turn.

Returning `None` instead of a `Quote` leaves everything as it is.

Prices are in ticks, quantities in lots, and cash and P&L in tick-lots. `on_start(info)` and
`on_end(result)` are optional.

## Test it offline

```bash
arena-kit markets
arena-kit test my_bot.py --market news --seeds 1-20 --duration 300
```

A local market is the same scenario a game uses, stepped one turn at a time. The same seed always
gives the same market and the same score. From Python:

```python
from arena_kit import run_local
print(run_local(MyBot(), "calm", seed=7))
```

## Play it in a game

```bash
arena-kit join my_bot.py --server http://host:8000 --code ABCDEF --name my-bot
```

The bot joins the game under that name and plays every round the host starts. The kit keeps the
bot's player token in `~/.crowdbook-arena/tokens.json`, so running the command again takes the
same seat.

In a live round, the market runs on the wall clock. The kit turns each `Quote` into orders: a
post-only order for each side, moved by a modify, and an immediate-or-cancel order for a take.
