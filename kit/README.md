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

Money is in tick-lots, and a tick is a cent. `state.balance` is what the bot started the round
with, and `state.equity` is that plus the round's P&L. `state.room("buy", price)` is how many lots
it can still afford to bid at a price. Quotes beyond buying power are cut to what the bot can
afford, offline and in a game alike; the arena's README explains the rule.

Prices are in ticks, quantities in lots, and cash and P&L in tick-lots. `on_start(info)` and
`on_end(result)` are optional. `info["briefing"]` is the round's briefing, the text players see
before it starts: who trades, the fees, the limits, when headlines come and how the score is
counted. It is the same offline, in lockstep rounds and in live ones.

### Several assets

In a `pair` or `basket` round, `on_turn` gets a `Portfolio` instead of a `State`:
`portfolio.assets` holds each asset's `State` by symbol, `portfolio.equity` counts every asset,
and `portfolio.room(symbol, side, price)` is the buying power on one asset after what the others
tie up. Answer with a dict of `Quote`s by symbol; an asset left out keeps its quotes.

```python
class PairBot(Bot):
    def on_turn(self, p):
        d, lan = p.assets["DRIFT"], p.assets["LANTERN"]
        if d.mid and lan.mid and lan.mid > 8000 + 0.8 * (d.mid - 10000) + 10:
            return {"LANTERN": Quote(take=-5)}  # Lanterns dear next to Driftwood: sell some
```

`on_start(info)["assets"]` names the assets. Turns come every simulated second and whenever one of
the bot's orders fills in any asset; every asset is shown as of that moment or before, never
after, offline, in lockstep and live alike.

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

## Practice data

```bash
arena-kit practice --market news --seeds 1-5 --out data/
```

records each public market as it runs for a seed with nobody else in it, every quarter second of
simulated time, into `data/`:
- `news-1-book.csv`: the best bid and offer, the mid, the last trade, the lots traded since the
  last row, and the true value, which no player sees during a round;
- `news-1-trades.csv`: every trade, with the side that crossed the spread;
- `news-1-headlines.csv`, in markets with news: each headline and when it came out (`--news` sets
  how late, as the host does);
- `news-1-briefing.txt`: the briefing.

The same seed always gives the same files. For `pair` and `basket`, each asset gets its own
files (`pair-1-DRIFT-book.csv`, `pair-1-LANTERN-book.csv`, ...), all from the same round. From Python, `arena_kit.practice.record("news",
seed=1)`. Only the public kinds can be recorded.

## Play it in a game

```bash
arena-kit join my_bot.py --server http://host:8000 --code ABCDEF --name my-bot
```

Use `http://` for a game served from a laptop or your own machine, and `https://` only for a hosted
server with a certificate; the kit says so if you mix them up. The bot joins the game under that
name and plays every round the host starts. The kit keeps the
bot's player token in `~/.crowdbook-arena/tokens.json`, so running the command again takes the
same seat.

Or, with no command at all, paste the file into the "Your bot" panel on the game page and press
Deploy: it runs in the browser tab, in Pyodide, and plays as it would from `arena-kit join`. It can
import the kit's `Bot`, `Quote`, `State`, `News`, `Fill` and `Trade`, and Python's standard
library; `run_local` and other packages are not there.

In a live round, the market runs on the wall clock. The kit turns each `Quote` into orders: a
post-only order for each side, moved by a modify, and an immediate-or-cancel order for a take.
