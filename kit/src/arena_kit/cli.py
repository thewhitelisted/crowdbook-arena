"""arena-kit: test a bot offline, or play it in a game.

    arena-kit markets
    arena-kit test my_bot.py --market news --seeds 1-5
    arena-kit practice --market news --seeds 1-5 --out data/
    arena-kit join my_bot.py --server http://localhost:8000 --code ABCDEF --name my-bot

A bot file defines `bot`, an instance of arena_kit.Bot, or a single Bot subclass to make one of.
`join` keeps the player token it is given in ~/.crowdbook-arena/tokens.json (or
$ARENA_KIT_HOME), so that running it again takes the same seat.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import ssl
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .bot import Bot, bot_from_module
from .client import run
from .markets import PUBLIC_KINDS
from .practice import record_all
from .stepped import run_local


def load_bot(path: str) -> Bot:
    spec = importlib.util.spec_from_file_location("arena_bot", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).resolve().parent))
    spec.loader.exec_module(module)
    try:
        return bot_from_module(module)
    except ValueError as e:
        raise SystemExit(f"{path}: {e}") from None


def seeds(text: str) -> list[int]:
    if "-" in text:
        lo, hi = text.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in text.split(",")]


def _home() -> Path:
    return Path(os.environ.get("ARENA_KIT_HOME", Path.home() / ".crowdbook-arena"))


def _no_https(server: str) -> SystemExit:
    """The error for an https:// address whose server answers in plain HTTP, as a game run on a
    laptop or another machine of one's own does."""
    plain = "http://" + server.split("://", 1)[1]
    return SystemExit(
        f"{server} does not answer over HTTPS. A game run on a laptop or your own machine is "
        f"served over plain HTTP: use --server {plain}. Only a hosted server with a certificate "
        f"(docs/hosting.md) uses https://.")


def _unreachable(server: str, reason: object) -> SystemExit:
    return SystemExit(f"cannot reach {server}: {reason}. Check the address and port, and that "
                      f"the server is running and reachable from this machine.")


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(data).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get("error", str(e))
        except ValueError:
            message = str(e)
        raise SystemExit(f"the server said: {message}") from None
    except urllib.error.URLError as e:
        server = url.split("/api/", 1)[0]
        if isinstance(e.reason, ssl.SSLError):
            raise _no_https(server) from None
        raise _unreachable(server, e.reason) from None


def join_token(server: str, code: str, name: str) -> str:
    """The token for this name in this game: the one kept from before, or a new one."""
    home = _home()
    store = home / "tokens.json"
    tokens = json.loads(store.read_text()) if store.exists() else {}
    key = f"{server.rstrip('/')}|{code.upper()}|{name}"
    if key not in tokens:
        reply = _post(f"{server.rstrip('/')}/api/games/{code.upper()}/join",
                      {"name": name, "kind": "bot"})
        tokens[key] = reply["token"]
        home.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(tokens, indent=1))
        os.chmod(store, 0o600)
    return tokens[key]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="arena-kit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("markets", help="list the public market kinds")
    t = sub.add_parser("test", help="run a bot in local markets")
    t.add_argument("bot")
    t.add_argument("--market", default="calm", choices=sorted(PUBLIC_KINDS))
    t.add_argument("--seeds", default="1-5", help="e.g. 7, 1,2,3 or 1-10")
    t.add_argument("--duration", type=int, default=300, help="simulated seconds")
    t.add_argument("--bankroll", type=float, default=10_000, help="dollars to start with")
    t.add_argument("--news", default="experts", choices=["instant", "experts", "crowd", "off"],
                   help="when headlines come, in markets with news")
    p = sub.add_parser("practice", help="record public markets as CSV files to study")
    p.add_argument("--market", default="calm", choices=sorted(PUBLIC_KINDS))
    p.add_argument("--seeds", default="1-5", help="e.g. 7, 1,2,3 or 1-10")
    p.add_argument("--duration", type=int, default=300, help="simulated seconds")
    p.add_argument("--news", default="experts", choices=["instant", "experts", "crowd", "off"],
                   help="when headlines come, in markets with news")
    p.add_argument("--out", default="practice", help="the folder to write to")
    j = sub.add_parser("join", help="play a bot in a game")
    j.add_argument("bot")
    j.add_argument("--server", required=True, help="e.g. http://localhost:8000")
    j.add_argument("--code", required=True, help="the game's join code")
    j.add_argument("--name", required=True, help="the bot's name in the game")
    j.add_argument("--token", help="a player token, instead of joining under --name")
    j.add_argument("--rounds", type=int, help="stop after this many rounds")
    args = parser.parse_args(argv)

    if args.command == "markets":
        for kind in PUBLIC_KINDS.values():
            print(f"{kind.name:10} {kind.title}: {kind.description}")
    elif args.command == "test":
        bot = load_bot(args.bot)
        scores = []
        for seed in seeds(args.seeds):
            r = run_local(bot, args.market, seed=seed, duration_s=args.duration, news=args.news,
                          bankroll=args.bankroll)
            scores.append(r.score)
            print(f"seed {seed:5}: score {r.score:+10.1f}  turns {r.turns:5}  "
                  f"position {r.position:+4}  fees {r.fees:+8.1f}")
        if len(scores) > 1:
            print(f"mean {statistics.mean(scores):+.1f}, sd {statistics.stdev(scores):.1f} "
                  f"over {len(scores)} markets")
    elif args.command == "practice":
        if not 5 <= args.duration <= 7200:
            raise SystemExit("--duration: from 5 to 7200 simulated seconds")
        for seed in seeds(args.seeds):
            for rec in record_all(args.market, seed, args.duration, args.news):
                paths = rec.write(args.out)
                print(f"seed {seed:5}{' ' + rec.asset if rec.asset else ''}: {len(rec.ticks)} "
                      f"ticks, {len(rec.trades)} trades, {len(rec.headlines)} headlines -> "
                      f"{', '.join(p.name for p in paths)}")
    elif args.command == "join":
        bot = load_bot(args.bot)
        token = args.token or join_token(args.server, args.code, args.name)
        print(f"{args.name} is in game {args.code.upper()}; waiting for rounds (ctrl-c to stop)")
        try:
            for r in run(bot, args.server, token, rounds=args.rounds):
                mine = r["result"]
                if mine:
                    print(f"round {r['round']['number']}: rank {mine['rank']} of "
                          f"{len(r['results'])}, score {mine['score']:+.1f}")
        except KeyboardInterrupt:
            pass
        except ssl.SSLError:
            raise _no_https(args.server.rstrip("/")) from None
        except ConnectionRefusedError as e:
            raise _unreachable(args.server.rstrip("/"), e) from None
        except ConnectionError as e:  # the server turned the bot away, and said why
            raise SystemExit(f"the server said: {e}") from None
        except OSError as e:
            raise _unreachable(args.server.rstrip("/"), e) from None


if __name__ == "__main__":
    main()
