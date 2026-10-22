# Handoff

Where the arena stands, for whoever picks it up next. Work paused on 22 Oct 2026, until about
next month.

## State

- **main at `eeb81eb`**, plus the commit that adds this file.
- **107 tests** (`uv sync --group browser && uv run pytest`, about 100 s), green in CI on Linux
  (browser tests included), on macOS on Apple silicon (installing from wheels only), and in Docker
  (a five-second live round inside the image, and the compose files with and without Postgres).
- **Engine pin:** `crowdbook-engine==0.14.1`, in `pyproject.toml` and `kit/pyproject.toml`.
- **Licence:** PolyForm Shield 1.0.0.

## What is built

- Games with join codes and a bankroll per player.
- **Round kinds:**
  - live (by hand and by bots, with a briefing every connected player confirms before the market
    opens);
  - lockstep (bots only; a bot's score equals its offline score);
  - challenges (sealed bids, round trip, stake).
- **Markets:**
  - Sea Salt, Pearls (with headlines) and Coral;
  - the pair (Driftwood & Lanterns) and the basket (Fruit Crate), which are several linked
    assets traded from one bankroll.
- **Leaderboards:** overall by balance; live, algorithmic and challenge boards by points.
- **The players' kit (`kit/`):**
  - `arena-kit test`, `join` and `practice`;
  - the `Bot`, `State` and `Portfolio` API;
  - a careful starter bot that profits modestly.
- **Bots pasted into the game page**, run in the player's own tab with Pyodide. The server never
  runs player code.
- **[The host kit](host-kit.md):** a 60–90 minute session plan for clubs, classes and recruiting
  events.
- **Hosting groundwork ([hosting.md](hosting.md)):** Docker, Caddy, optional Postgres. It is not
  deployed anywhere.

## Invariants the tests hold

1. A bot's lockstep score equals its `run_local` score, for the same seed, market, news setting and
   bankroll. Pairs and baskets are included.
2. No bot sees its market (or, in a pair or basket, any of its assets) ahead of another moment it
   has been shown, and lockstep bots move through a round together.
3. A player acts only from their own seat: the seat comes from the token, never from a message.
4. No challenge score or solution is sent before the challenge closes.
5. The server never runs player code.
6. Everything installs from wheels on an Apple-silicon Mac.
7. `tests/test_yardstick.py`: a flow-following market maker must not win every round in any
   public market.

## Decided

- **One bankroll per game, in dollars.**
  - Buying power: open orders are counted as if they filled.
  - With several assets, each asset's buying power is the equity less what the others tie up.
- **News delay:** "with the experts" by default.
- **Challenges are for people only.**
- **Lockstep gives each bot its own copy of the market.** A shared lockstep book would need a
  multi-seat lockstep mode in the engine.

## Not possible yet: a static demo

The game cannot run as a static site (for example GitHub Pages). Bots already run in the browser,
but the markets are crowdbook's native engine: `crowdbook-engine` publishes wheels for macOS
arm64 and Linux only, and Pyodide has no build of it. A free browser-only demo needs a
WebAssembly (Pyodide/emscripten) build of the engine. Until then, a demo needs a server.

## Next, when work resumes

- **Options:** the next feature, built only when a prospective user asks for it.
- **Open threads:**
  - refuse the Postgres default password `change-me` when the postgres profile is on;
  - open challenges are lost when the server restarts (they could be stored).

## Working rules

- **Commits:** authored and committed by `thewhitelisted <clee2k6@outlook.com>`. Messages start
  with `add:`, `update:` or `fix:`. No assistant attribution in commits, PR titles or bodies, or
  file headers (see CLAUDE.md).
- **PRs:** one per change, squash-merged when CI is green on Linux, macOS and Docker.
- **Keep it public:** this repository is public. Strategies, research and private market kinds
  stay out of it; private kinds plug in at deploy time through the
  `crowdbook_arena.market_kinds` entry point (hosting.md).
