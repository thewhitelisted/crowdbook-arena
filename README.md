# crowdbook-arena

A trading competition on [crowdbook](https://github.com/thewhitelisted/crowdbook) markets, for a
group of friends or a club, after the style of IMC Prosperity. Someone starts a game and shares its
code. Everyone then trades in the same live, simulated order-book market, by hand in the browser
or with a Python bot. The engine's own traders (market makers, noise traders, trend followers,
traders who know the true value early or late) give the book its liquidity.

The server runs the markets. Players run their own bots on their own machines and connect over
the network, and the server never runs anyone's code.

> **Status:** done so far:
> - games with join codes;
> - live rounds traded by hand and by bots, with results;
> - lockstep rounds for bots;
> - manual challenges;
> - leaderboards across rounds;
> - the players' kit;
> - the groundwork for hosting: Docker, and Caddy for HTTPS ([docs/hosting.md](docs/hosting.md)).

## Run it

Python 3.12 or later and [uv](https://docs.astral.sh/uv/). On Linux (x86-64 or arm64) or macOS on
Apple silicon, everything installs from ready-built wheels, with nothing to compile.

```bash
git clone https://github.com/thewhitelisted/crowdbook-arena && cd crowdbook-arena
uv run crowdbook-arena
```

Open <http://127.0.0.1:8000>:

1. Start a game. You are its host, and it gets a join code like `3Q3GRD`.
2. Open the game's link in a second tab or another browser and join under another name, or send
   the link to friends on the same network. Start the server with `ARENA_HOST=0.0.0.0` so others
   can reach it.
3. As host, pick a round, a market and a length, and start it.
   - A live round runs on the clock, at the speed you choose, and every player in the game gets
     a seat in it.
   - A lockstep round is for the game's bots (see [Bots](#bots)).
   - A challenge round is a one-off decision for everyone (see [Challenges](#challenges)).
4. Trade from the ladder or the order ticket:
   - Click a bid size to bid at that price, or an offer size to offer at it.
   - Click your own order (in yellow) to cancel it.
   - Use the ticket for market orders.
   The screen shows your position, P&L, cash, fees, open orders, the tape and the clock.
5. At the end, everyone sees the results.

Each tab is its own player. A tab that is closed can carry on as the same player from the game's
page. A player whose last tab or bot disconnects has their open orders cancelled, and keeps their
position and seat for when they come back.

## Bots

Bots use the kit, [`crowdbook-arena-kit`](kit/README.md). A bot is a class with one method: given
the market and the bot's account at each turn, it returns the bid and offer it wants resting and
anything it wants to take now.

```python
from arena_kit import Bot, Quote

class MyBot(Bot):
    def on_turn(self, state):
        if state.bid and state.ask:
            return Quote(bid=(state.bid[0], 2), ask=(state.ask[0], 2))
```

```bash
uv run arena-kit test kit/examples/starter_bot.py --market news --seeds 1-10   # offline
uv run arena-kit join kit/examples/starter_bot.py --server http://127.0.0.1:8000 \
    --code 3Q3GRD --name starter                                              # in the game
```

The same code runs offline and in a game, unchanged.

### Lockstep rounds

In a lockstep round, which is for bots, each bot trades its own copy of the same market, from the
same seed, as in IMC Prosperity.

At every turn the market waits for the bot. A turn is each simulated second, and whenever one of
the bot's orders fills. The bot's answer reaches the market at the turn's simulated time, so how
far away the bot is, or how long it thinks, changes nothing as long as it answers within the
round's turn timeout. A late answer, or none, leaves its quotes as they were and takes nothing.

A bot's score in a lockstep round is exactly the score `arena-kit test` gives it offline on the
same market and seed, and the tests check this. All the copies move through the market together, one simulated second at a time. No bot is shown
a moment of its market until every bot has finished the second before it, so no bot can run ahead
and tell another what is coming. A bot that disconnects is waited for a few seconds, then stands
aside at the others' pace until it comes back.

Later, perhaps: a shared lockstep market, where every bot trades the same book turn by turn. That
needs a lockstep gateway mode with several seats in crowdbook itself, which the public engine does
not have yet.

## Challenges

A challenge round puts one decision to every person in the game, the host included, after IMC
Prosperity's manual challenges. Bots sit challenges out.
Each challenge is drawn from the round's seed and has an exact best answer.

| Challenge | The decision | What it is worth |
|---|---|---|
| Sealed bids | A bid in a first-price auction against rivals whose bids are uniform on a range you are told | The profit you expect |
| Round trip | A path of up to five trades through a table of exchange rates, from Shells back to Shells | The Shells you end with |
| Stake | The fraction of your wealth to stake, again and again, on a bet with known odds | The growth you expect, per bet |

Players can change their answer until the challenge closes, on its timer or when the host ends it.
Only then are the scores and the best answer shown. A score puts the answer's worth on a scale
from doing nothing, 0, to the best answer, 100, held between -100 and 100.

## Money

Every player has one bankroll for the whole game, the same for everyone. The host sets it when
starting the game, $10,000 unless changed. A tick is a cent, so prices and money show in dollars:
a price of 10,000 ticks is $100.00 a lot.

Each live or lockstep round adds its P&L after fees to the balance. Challenge rounds score out of
100 and leave money alone.

**Buying power.** Counting your open orders as if they all filled, you may hold at most as many
lots either way as your equity buys at the order's price. Equity is your balance at the start of
the round, plus the round's cash and position at the middle of the market, less fees. A long
position is paid for in full, and a short one needs the same amount set aside. The position limit
of 100 lots still applies on top.

Orders beyond buying power are refused with a message. A player whose equity does not cover a
single lot is bust: they can watch and close positions, but cannot open any.

The same rule holds for people and bots, in live rounds, in lockstep rounds, and offline:
`run_local(..., bankroll=10_000)` and `arena-kit test --bankroll`. In lockstep and offline it cuts
a bot's quotes down to what it can afford.

## Leaderboards

Each game has four leaderboards:
- live trading (the owner's "manual" board);
- algorithmic (lockstep rounds);
- challenges;
- overall, which ranks by balance.

A market round's scores are in tick-lots, which differ from one market to the next, so each market
round counts by rank. In a round of n players, first place gets 100 points and last gets 0, with
the rest spread evenly between and ties sharing. A challenge already scores out of 100, so its
score counts as it is. Each board adds up a player's points over its rounds.

## Markets

Public market kinds come from the kit (`arena-kit markets`):

| Kind | What it is |
|---|---|
| `calm` | A few trades a second around a slowly wandering true value. |
| `news` | The true value jumps about once a minute. Experts see the jump early, the crowd late. Players can get a headline too (see below). |
| `trending` | A livelier value and many trend followers, so prices overshoot. |

In the `news` market each jump in the true value comes with a headline, such as "Good news: value
up about 8". The host chooses when players see it:
- at once;
- with the experts, about 2 seconds late (the default);
- with the crowd, about 30 seconds late;
- never.

Screens show headlines in a news panel and on the price chart. Bots get them in `State.news`, at
the same moments live, in lockstep and offline (`arena-kit test --news`). The arena draws the news
market's value path itself, so that it knows each jump, and gives it to crowdbook as a recorded
path. That needs crowdbook 0.14 or later. With 0.13, the news market's jumps come from crowdbook
itself and there are no headlines.

A Python package installed on the server can add kinds of its own through the
`crowdbook_arena.market_kinds` entry point group (see `src/crowdbook_arena/kinds.py`). Those kinds
stay on the server: players cannot read them or test against them offline.

Scores are profit after fees, in tick-lots, with any position left at the end valued at what
closing it through the book would bring.

## Settings

All settings come from environment variables:

| Variable | Default | |
|---|---|---|
| `ARENA_HOST`, `ARENA_PORT` | `127.0.0.1`, `8000` | Where the server listens. |
| `ARENA_DATABASE_URL` | `sqlite:///data/arena.db` | Any SQLAlchemy URL. For Postgres, `postgresql+psycopg://user:pass@host/db` with the `postgres` extra. |
| `ARENA_WORK_DIR` | `data/rounds` | Each live round's scenario, session recording and results. |
| `ARENA_MARKET_KINDS` | all | A comma-separated list of the kinds games may use. |
| `ARENA_MAX_PLAYERS` | `32` | Players a game may have. |
| `ARENA_MAX_LIVE_DURATION_S` | `3600` | The longest live round. |
| `ARENA_MAX_LOCKSTEP_DURATION_S` | `7200` | The longest lockstep round, in simulated seconds. |
| `ARENA_LOCKSTEP_TIMEOUT_MS` | `2000` | A lockstep turn's timeout, unless the round sets its own. |
| `ARENA_MAX_CHALLENGE_DURATION_S` | a week | The longest a challenge can stay open. |
| `ARENA_RATE_LIMIT` | `50` | Messages a seat may send a second. |
| `ARENA_FEED_INTERVAL_MS` | `100` | How often market data is batched to screens. |
| `ARENA_CROWDBOOK` | the venv's | The `crowdbook` command that serves live rounds. |

## How it works

- **Live rounds.** Each live round is a `crowdbook serve` process on the loopback interface, with
  one seat per player. The arena claims every seat itself, with tokens only it knows, and relays
  each seat's gateway messages to its player's screens and bots over a WebSocket. Players' order
  requests are checked field by field before they reach the gateway. A player can only send from
  their own seat, because the seat comes from their token and never from the message.
- **Lockstep rounds.** Each bot's market is a `crowdbook_learn.Pool` of one market, stepped
  in-process (`src/crowdbook_arena/lockstep.py`).
- **Players and tokens.** A player is identified by a random token, given once when they create
  or join a game. The server keeps only its SHA-256 hash.
- **Storage.** SQLite, through SQLAlchemy Core (`src/crowdbook_arena/db.py`).
- **Front end.** Plain HTML and JavaScript modules in `src/crowdbook_arena/static`, with no build
  step: a ladder, a ticket and a few tables fed by one WebSocket.

## Development

```bash
uv sync --group browser && uv run playwright install chromium   # the browser test's Chromium
uv run pytest
```

CI runs the tests on Linux, browser test included, and on macOS on Apple silicon, where it also
checks that every dependency installs from a wheel.

## License

[PolyForm Shield 1.0.0](LICENSE).
