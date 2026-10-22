# Host kit: a 60–90 minute trading session

This guide is for whoever runs a session of crowdbook arena for a group: a trading club, a class,
or a recruiting event. It covers what to set up, which rounds to run and in what order, how
scoring works, and what to ask afterwards. An optional bot segment at the end adds 15–20 minutes
for groups who write code.

Everyone plays in a web browser. Only the host needs to install anything.

## What you need

- **A laptop to run the game**, on Linux or macOS on Apple silicon, with Python 3.12 or later and
  [uv](https://docs.astral.sh/uv/). Or anything that runs Docker (see [hosting.md](hosting.md)).
- **A network everyone can reach the laptop on.** A room's Wi-Fi works if it lets devices see each
  other; a phone's hotspot works for a small group. For a group spread over several places, put
  the game on a server with a domain name, as [hosting.md](hosting.md) describes.
- **A screen to project the host's view:** the lobby, the leaderboard and each round's results.
- **Players' devices:** a laptop is best. A phone works for the hand-traded rounds.
- **6 to 30 players** is the comfortable range. A game takes up to 32 by default
  (`ARENA_MAX_PLAYERS`).

## Before the session (15 minutes, the day before if you can)

1. Start the game server so that others on the network can reach it:

   ```bash
   git clone https://github.com/thewhitelisted/crowdbook-arena && cd crowdbook-arena
   ARENA_HOST=0.0.0.0 uv run crowdbook-arena
   ```

   It prints the address it listens on. Others use the laptop's address on the network, for
   example `http://192.168.1.20:8000`. Note it is `http://`, not `https://`: a game run from a laptop
   has no certificate. Only a hosted server (hosting.md) uses `https://`.
2. Open the address, start a game with your name, and keep the bankroll at $10,000. You are the
   host. The game gets a join code like `3Q3GRD`.
3. **Test it from a phone on the same network:** open the game's link, join under a test name, and
   check that you see the lobby. If the phone cannot connect, see [Troubleshooting](#troubleshooting).
4. Run one short round to see the screens: a live round, Sea Salt, 60 seconds, speed 1. Read its
   briefing, press "I'm ready", and place a bid and a market buy.

The test player stays in the game's player list. Use a fresh game for the session itself.

## The session at a glance

| Time | What | Round settings (lobby) |
|---|---|---|
| 0:00 | Welcome, join, a tour of the screen | — |
| 0:08 | **Round 1: warm-up** | Live, Sea Salt, 120 s, speed 1 |
| 0:12 | Debrief 1 (short) | — |
| 0:15 | **Round 2: the calm market** | Live, Sea Salt, 300 s, speed 1 |
| 0:21 | Debrief 2 | — |
| 0:27 | **Challenge: sealed bids** | Challenge, Sealed bids, 180 s |
| 0:31 | **Round 3: news** | Live, Pearls, 300 s, speed 1, news "with the experts" |
| 0:37 | Debrief 3 | — |
| 0:43 | **Round 4: trends** | Live, Coral, 300 s, speed 1 |
| 0:49 | Debrief 4 | — |
| 0:55 | **Round 5: a pair** | Live, Driftwood & Lanterns, 300 s, speed 1 |
| 1:01 | **Challenge: stake** | Challenge, Stake, 180 s |
| 1:05 | Final leaderboard and debrief | — |
| 1:15 | *Optional:* bot segment | Lockstep, Sea Salt, 300 s |

For a 60-minute slot, drop Round 4 and the stake challenge. Speed 2 halves any live round's
wall-clock time: 300 simulated seconds take 2.5 minutes.

## Running it

### Welcome and joining (8 minutes)

Put the game's link and code on the projector ("Copy invite link" in the header). Players open it,
type a name and join. The lobby's player list fills in as they arrive, each with a green dot while
connected.

Tour the trading screen while people join. Show it from the warm-up round or from a screenshot.

- **Top strip:** the clock, your position, your P&L this round, equity, fees.
- **Order book (left):** bids in green below the price, offers in red above. Click a size in the
  book to place an order at that price; click your own order (yellow) to cancel it.
- **Chart (middle):** the middle of the market, with your fills marked.
- **Trade ticket (right):** lots and a price, then "Bid at price" and "Offer at price" to rest an
  order, or "Buy now" and "Sell now" to trade at once against the book. Your open orders are
  listed below.
- **Below:** the tape of trades, the news (in Pearls) and the round's briefing.

### Each live round

1. In the lobby, choose Round "Live", the market, the length in seconds, and the speed. For Pearls,
   choose when players hear the news. Press "Start the round".
2. **Everyone sees the briefing first.** It tells them what the asset is, how its price behaves,
   who else trades it, the fees, the limits, and how the score works. The market opens when every
   connected player has pressed "I'm ready". The screen shows who is still reading. Players who
   are not connected are not waited for. If someone is slow, "Start now" opens the market without
   them.
3. While it runs, the projector can show your own trading screen, or the lobby.
4. At the close, the results table shows each player's P&L after fees, return, position and fees,
   and the leaderboard updates. "End the round now" stops a round early, with results as they
   stand.

### The markets, and what each one teaches

- **Sea Salt (calm):** a fair value that drifts steadily, with no news. Market makers, a random
  crowd, a few trend followers, and well-informed traders who pick off quotes left behind. It
  teaches the basics: the spread, fees, buying power, and how inventory risk builds.
- **Pearls (news):** quiet stretches, then sharp jumps when news breaks, about once a minute.
  Headlines reach players at the time the host chooses: at once, with the experts (about 2
  seconds late, the default), with the crowd (about 30 seconds late), or never. Funds trade big
  orders one way for reasons of their own, so not every run of buying is news. It teaches
  information, speed and adverse selection.
- **Coral (trending):** volatile, with spells where the fair value runs one way and then turns,
  and trend followers who chase every move, so prices overshoot. It teaches momentum against mean
  reversion, and position sizing.
- **Driftwood & Lanterns (pair):** two assets in one round, with one bankroll. Lanterns follow
  about four fifths of Driftwood's moves, with a gap that wanders and comes back. Tabs above the
  book switch between the assets. It teaches relative value and hedging.
- **Fruit Crate (basket):** Coconuts, Mangoes, and a crate holding one of each, which trades near
  its contents' worth. It is an alternative to the pair, for a group that is moving quickly.

### Challenges

A challenge round puts one decision to every person at once: no market, just an answer typed into
a form before the timer ends. Players can change their answer until it closes. Then the best
answer is revealed, and each answer scores from 0 (doing nothing) to 100 (the best). Challenges do
not change anyone's money.

- **Sealed bids:** bid in a first-price auction against rivals whose bids are spread evenly over a
  range you are told. Bid too high and you overpay; too low and you lose the item.
- **Round trip:** a path of up to five trades through a table of exchange rates, from Shells back
  to Shells.
- **Stake:** the fraction of your wealth to stake, again and again, on a bet with known odds.

They make good breathers between live rounds, and good discussion: each has an exact answer to
reveal and argue about.

## Scoring

- Everyone starts the game with the same bankroll, $10,000 by default.
- Each live round adds its P&L after fees to a player's balance. A position still held at the
  close is valued at what selling or buying it back through the book would bring, so a big
  position in a thin book costs something to carry to the end.
- **Fees:** an order that rests in the book and is filled earns 0.1 tick (a tenth of a cent) a
  lot; an order that takes from the book pays 0.2 tick a lot.
- **Limits:** at most 100 lots long or short in each asset, and 20 lots an order. Buying power is
  also limited by equity: counting open orders as if they all filled, a player can hold only as
  many lots as their equity buys. A player whose equity does not cover a single lot is bust: they
  can close positions but not open new ones.
- **Leaderboards:**
  - *Overall* ranks by balance.
  - *Live trading* and *Algorithmic* rank by points: 100 for first in a round down to 0 for last.
  - *Challenges* add up challenge scores.

The overall board is the natural "winner" for the session. The live-trading board rewards
consistency across rounds rather than one lucky round.

## Debrief questions

Ask two or three after each round, and save the last set for the end.

**After the warm-up and Sea Salt**
- Who made money? Who mostly bought, who mostly sold, and who did both?
- What did the spread cost you each time you used "Buy now" or "Sell now"? What did fees add?
- If you rested orders in the book, which ones filled? Were you happy to be filled when you were?
- How large a position did you hold at the worst moment? Was that a decision or an accident?

**After Pearls**
- When did you hear the news, and what had the price already done by then?
- When a run of buying began, did you join it? Was it news or a fund?
- Who lost money on orders resting in the book when news broke? What would have protected you?
- How would the round have changed if the news had come at once? Or never?

**After Coral**
- When did you decide a move was a trend, and when that it had gone too far?
- Did trend followers help you or hurt you?
- How did you size your position as the price ran?

**After the pair or basket**
- How did you decide whether Lanterns were cheap or dear? Next to what?
- Did you trade both assets, or one of them? What did the second one do for your risk?
- What happened to your buying power when you held positions in both?

**After the challenges**
- Sealed bids: why is the best bid below what the item is worth to you?
- Stake: why does staking everything on a favourable bet still go wrong?

**To close the session**
- Which round was hardest, and why?
- What would you do differently with the same information?
- Where did your profit come from: the spread, information, trends or relative value? Where did
  your losses come from?
- If you had to hand your strategy to a computer, what rules would you give it?

The last question leads into the bot segment, if you run it.

## Optional: the bot segment (15–20 minutes)

For groups who write Python. Bots trade through the same seat rules as people, with the same
bankroll and buying power. The server never runs anyone's code.

**In the browser, nothing to install.** In the game page, open "Your bot", paste a bot file and
press "Deploy". It joins the game as a bot player named after its owner and runs in that player's
browser tab, which must stay open. The starter bot in the kit, `kit/examples/starter_bot.py`, is a
careful market maker that profits modestly; it is a good starting point to change one parameter
at a time.

**From a terminal.** Players who install the kit (`pip install` as its README shows) can test
offline against the same markets (`arena-kit test my_bot.py --market news --seeds 1-5`) and then
join (`arena-kit join my_bot.py --server http://... --code ...`). `arena-kit practice` writes
recorded markets as CSV to study.

**A lockstep round** is the fair way to compare bots. Each bot trades its own copy of the same
market from the same seed, and the market waits for every bot at every turn, so speed and
connection quality do not matter. A bot's score in a lockstep round equals what `arena-kit test`
gives it offline for the same market and seed. Choose Round "Lockstep", a market and a length,
and start it: only bots take part, and results arrive when every bot has finished.

A simple format: give players 10 minutes to change the starter bot, then run one lockstep round
on Sea Salt and one on Pearls, and compare.

## Troubleshooting

- **Players cannot reach the game.**
  - Check that the server was started with `ARENA_HOST=0.0.0.0`.
  - Check that players use the laptop's address on the network, with `http://` and the port
    (`:8000`).
  - Check that the network lets devices talk to each other: guest Wi-Fi often does not, while a
    phone hotspot does.
  - Check the laptop's firewall.
- **Someone used `https://` for a laptop server.** The browser shows a connection error; use
  `http://`.
- **Someone's tab closed mid-round.** Their open orders are cancelled; their position stays.
  Reopening the game link in the same browser carries on as the same player, and the join page
  lists the players this browser has been.
- **A player is bust.** They can still close positions in later rounds, and the leaderboard keeps
  their balance. To give everyone a fresh start, start a new game.
- **A round will not start because someone is not ready.** Press "Start now".
- **The room is slow or the laptop is old.** Use speed 1, keep rounds to 300 seconds, and run the
  pair (two markets) rather than the basket (three).
