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
> - the players' kit.
>
> Coming next: manual challenges, leaderboards across rounds, and the groundwork for hosting.

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
same market and seed, and the tests check this. A bot without a connection stands aside, so it
cannot hold up the round.

## Markets

Public market kinds come from the kit (`arena-kit markets`):

| Kind | What it is |
|---|---|
| `calm` | A few trades a second around a slowly wandering true value. |
| `news` | The true value jumps about once a minute. Experts see the jump early, the crowd late. |
| `trending` | A livelier value and many trend followers, so prices overshoot. |

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
