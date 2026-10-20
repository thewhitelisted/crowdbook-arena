"""Games in play: who is connected, the round under way, and what everyone is told."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket

from arena_kit import money
from arena_kit.markets import NEWS_DELAYS, MarketKind, briefing_of, has_news, headlines, is_linked

from .auth import new_code, new_token, token_hash
from .config import Settings
from .db import Store
from .live import LiveRound, MultiLiveRound, RequestError
from .challenges import KINDS as CHALLENGES, AnswerError, ChallengeRound, make as make_challenge
from .leaderboard import leaderboards
from .lockstep import LockstepRound

log = logging.getLogger(__name__)

NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-.'")
SEND_QUEUE = 2000  # messages a connection may fall behind by before it is dropped


LIVE = (LiveRound, MultiLiveRound)


class GameError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def check_name(name: Any, what: str = "name") -> str:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or set(name) - NAME_CHARS:
        raise GameError(f"{what}: 1 to 24 letters, digits, spaces, or _ - . '")
    return name.strip()


class Connection:
    """One WebSocket, written to by its own task from a queue, so that a slow reader never holds
    up a market."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(SEND_QUEUE)
        self.task = asyncio.create_task(self._pump())

    def send(self, msg: dict[str, Any]) -> bool:
        try:
            self.queue.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            return False

    async def _pump(self) -> None:
        try:
            while (msg := await self.queue.get()) is not None:
                await self.ws.send_json(msg)
        except Exception:
            pass

    async def close(self) -> None:
        self.task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


@dataclass
class ReadyCheck:
    """A live round between its start and its market's: players read the briefing, and the
    market opens once every human player still connected has said they are ready (or the host
    starts it). Bots are ready at once: they get the briefing in on_start."""

    players: list[int]                 # every seat in the round
    humans: set[int]                   # the seats that are asked
    ready: set[int] = field(default_factory=set)


class GameHub:
    def __init__(self, arena: "Arena", game: dict[str, Any]):
        self.arena = arena
        self.game = game
        self.conns: dict[int, set[Connection]] = {}
        self.round: dict[str, Any] | None = None
        self.runner: LiveRound | MultiLiveRound | LockstepRound | ChallengeRound | None = None
        self.ready_check: ReadyCheck | None = None
        self.lock = asyncio.Lock()

    # Connections

    def online(self) -> set[int]:
        return {pid for pid, cs in self.conns.items() if cs}

    async def attach(self, player: dict[str, Any], conn: Connection) -> None:
        self.conns.setdefault(player["id"], set()).add(conn)
        conn.send({"type": "welcome", "player": player, "game": self.info()})
        conn.send({"type": "leaderboard", "boards": self.leaderboard()})
        runner = self.runner
        if isinstance(runner, LIVE) and self.round and self.ready_check is None:
            snap = runner.snapshot(player["id"])
            conn.send({"type": "live_snapshot", "round": self.round["id"], "seated": snap is not None,
                       "state": snap or runner.any_snapshot()})
        elif isinstance(runner, LockstepRound) and self.round:
            runner.reconnected(player["id"])
            conn.send({"type": "lockstep_progress", "round": self.round["id"],
                       **runner.progress()})
        elif isinstance(runner, ChallengeRound):
            conn.send(runner.public(player["id"]))
        await self.broadcast_info()

    async def detach(self, player_id: int, conn: Connection) -> None:
        conns = self.conns.get(player_id, set())
        conns.discard(conn)
        await conn.close()
        if not conns and self.ready_check is not None:
            # Gone during the briefing: no longer waited for.
            try:
                await self.begin_if_ready()
            except GameError:
                pass
        elif not conns and isinstance(self.runner, LIVE):
            # No screen or bot left to watch the seat's orders: take them out of the book.
            await self.runner.cancel_all(player_id)
        elif not conns and isinstance(self.runner, LockstepRound):
            self.runner.disconnected(player_id)
        await self.broadcast_info()

    async def send(self, player_id: int, msg: dict[str, Any]) -> None:
        for conn in list(self.conns.get(player_id, ())):
            if not conn.send(msg):
                log.warning("dropping a connection of player %s that fell behind", player_id)
                self.conns[player_id].discard(conn)
                # Not awaited: a client that does not read must never hold up a market's relay.
                asyncio.create_task(conn.close())

    async def broadcast(self, msg: dict[str, Any]) -> None:
        for pid in list(self.conns):
            await self.send(pid, msg)

    async def broadcast_info(self) -> None:
        await self.broadcast({"type": "game", "game": self.info()})

    # What everyone sees

    def info(self) -> dict[str, Any]:
        store = self.arena.store
        online = self.online()
        players = [{"id": p["id"], "name": p["name"], "kind": p["kind"], "is_host": p["is_host"],
                    "online": p["id"] in online, "balance": p["balance"],
                    "bust": money.max_lots(p["balance"], money.LOT_PRICE) == 0}
                   for p in store.players(self.game["id"])]
        return {
            "code": self.game["code"], "name": self.game["name"], "players": players,
            "bankroll": store.game(self.game["id"])["bankroll"],
            # Past rounds without their briefings, which would only make every update longer.
            "rounds": [round_info(r, with_briefing=False) for r in store.rounds(self.game["id"])],
            "current": round_info(self.round) if self.round else None,
            "ready_check": self.ready_info(),
            "markets": [{"name": k.name, "title": k.title, "description": k.description,
                         "news": has_news(k)} for k in self.arena.kinds.values()],
        }

    # Rounds

    async def start_round(self, host: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        if not host["is_host"]:
            raise GameError("only the game's host can start a round", 403)
        settings = self.arena.settings
        async with self.lock:
            if self.round is not None:
                raise GameError("a round is already under way", 409)
            kind = spec.get("kind", "live")
            if kind not in ("live", "lockstep", "challenge"):
                raise GameError(f"unknown kind of round {kind!r}")
            seed = spec.get("seed")
            seed = _int(seed, "seed", 0, 2**31 - 1) if seed is not None else secrets.randbelow(2**31)
            players = self.arena.store.players(self.game["id"])
            if kind == "challenge":
                return await self._start_challenge(spec, seed, players)
            market = spec.get("market", "calm")
            if market not in self.arena.kinds:
                raise GameError(f"unknown market {market!r}")
            duration = _int(spec.get("duration_s", 300), "duration_s", 5,
                            settings.max_live_duration_s if kind == "live"
                            else settings.max_lockstep_duration_s)
            kind_obj: MarketKind = self.arena.kinds[market]
            config: dict[str, Any] = {"duration_s": duration, "market_title": kind_obj.title}
            news: list = []
            if has_news(kind_obj):
                delay = spec.get("news", "experts")
                if delay not in NEWS_DELAYS:
                    raise GameError(f"news: one of {', '.join(NEWS_DELAYS)}")
                config["news"] = delay
            config["briefing"] = briefing(kind_obj, seed, duration, config.get("news"))
            linked = is_linked(kind_obj)
            if linked:
                config["assets"] = {a.symbol: a.title for a in kind_obj.assets}
            if kind == "live":
                speed = spec.get("speed", 1)
                if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                        or not 0.1 <= speed <= 100):
                    raise GameError("speed: simulated seconds a second, from 0.1 to 100")
                config["speed"] = speed
            else:
                players = [p for p in players if p["kind"] == "bot"]
                if not players:
                    raise GameError("a lockstep round is for bots, and this game has none yet")
                config["turn_timeout_ms"] = _int(spec.get("turn_timeout_ms",
                                                          settings.lockstep_timeout_ms),
                                                 "turn_timeout_ms", 50, 60_000)
            if not players:
                raise GameError("no players")
            rnd = self.arena.store.add_round(self.game["id"], kind, market, seed, config)
            scenario = (kind_obj.scenarios(seed, duration) if linked
                        else kind_obj.scenario(seed, duration))
            if "news" in config:
                news = headlines(kind_obj, seed, duration, config["news"])
            balances = {p["id"]: p["balance"] for p in players}
            on_end = lambda scores: self._round_ended(rnd, scores, money=True,  # noqa: E731
                                                      balances=balances)
            try:
                if kind == "live":
                    common = dict(
                        round_id=rnd["id"], seed=seed, duration_s=duration,
                        speed=config["speed"], player_ids=[p["id"] for p in players],
                        crowdbook=settings.crowdbook, work_dir=settings.work_dir, send=self.send,
                        on_end=on_end, rate_limit=settings.rate_limit,
                        feed_interval_ms=settings.feed_interval_ms, watch=self._watchers,
                        balances=balances, announce=self.broadcast)
                    runner: LiveRound | MultiLiveRound | LockstepRound = (
                        MultiLiveRound(scenarios=scenario, **common) if linked
                        else LiveRound(scenario=scenario, news=news, **common))
                else:
                    runner = LockstepRound(
                        round_id=rnd["id"], scenario=scenario, seed=seed,
                        player_ids=[p["id"] for p in players],
                        timeout_ms=config["turn_timeout_ms"], send=self.send,
                        connected=lambda pid: bool(self.conns.get(pid)), news=news,
                        balances=balances,
                        on_progress=self._lockstep_progress, on_end=on_end)
                self.round, self.runner = rnd, runner
                if isinstance(runner, LIVE):
                    # The market waits while the players read the briefing.
                    self.ready_check = ReadyCheck(
                        [p["id"] for p in players],
                        {p["id"] for p in players if p["kind"] == "human"})
                else:
                    await runner.start()
                await self.broadcast({"type": "round_started", "round": round_info(rnd)})
            except BaseException as e:
                log.exception("round %s did not start", rnd["id"])
                self.round = self.runner = None
                self.arena.store.finish_round(rnd["id"], "failed", {})
                await self.broadcast_info()
                if not isinstance(e, Exception):
                    raise
                raise GameError(f"the round did not start: {e}", 500) from e
        if isinstance(runner, LIVE):
            await self.begin_if_ready()  # at once if nobody needs to read the briefing
        await self.broadcast_info()
        return round_info(rnd)

    # The briefing before a live round's market opens

    def waiting(self) -> list[int]:
        """The players the market still waits for: connected, asked, and not yet ready."""
        check, online = self.ready_check, self.online()
        if check is None:
            return []
        return sorted(pid for pid in check.humans if pid in online and pid not in check.ready)

    def ready_info(self) -> dict[str, Any] | None:
        check = self.ready_check
        if check is None or self.round is None:
            return None
        return {"round": self.round["id"], "asked": sorted(check.humans),
                "ready": sorted(check.ready), "waiting": self.waiting()}

    async def ready(self, player: dict[str, Any]) -> None:
        """A player has read the briefing."""
        check = self.ready_check
        if check is None:
            raise RequestError("no round is waiting for its players")
        if player["id"] in check.humans:
            check.ready.add(player["id"])
        try:
            if not await self.begin_if_ready():
                await self.broadcast_info()
        except GameError as e:
            raise RequestError(str(e)) from None

    async def begin_now(self, host: dict[str, Any]) -> None:
        """The host starts the market without waiting for everyone."""
        if not host["is_host"]:
            raise GameError("only the game's host can start the market", 403)
        if self.ready_check is None:
            raise GameError("no round is waiting for its players", 409)
        await self.begin_if_ready(force=True)

    async def begin_if_ready(self, force: bool = False) -> bool:
        """Opens the market of the live round waiting for its players, if nobody is still
        reading (or `force`); True if it opened. Raises GameError if it could not start."""
        async with self.lock:
            check, rnd, runner = self.ready_check, self.round, self.runner
            if check is None or rnd is None or not isinstance(runner, LIVE):
                return False
            if not force and self.waiting():
                return False
            self.ready_check = None
            try:
                await runner.start()
            except BaseException as e:
                log.exception("round %s did not start", rnd["id"])
                self.round = self.runner = None
                self.arena.store.finish_round(rnd["id"], "failed", {})
                await self.broadcast_info()
                if not isinstance(e, Exception):
                    raise
                raise GameError(f"the round did not start: {e}", 500) from e
            for pid in check.players:
                await self.send(pid, {"type": "live_snapshot", "round": rnd["id"],
                                      "seated": True, "state": runner.snapshot(pid)})
        await self.broadcast_info()
        return True

    def _cancel_ready_check(self) -> bool:
        """Ends a live round that has not opened its market yet, as failed; True if there was
        one."""
        if self.ready_check is None or self.round is None:
            return False
        self.arena.store.finish_round(self.round["id"], "failed", {})
        self.ready_check = None
        self.round = self.runner = None
        return True

    async def _start_challenge(self, spec: dict[str, Any], seed: int,
                               players: list[dict[str, Any]]) -> dict[str, Any]:
        """Starts a challenge round, for the game's human players; called with the lock
        held."""
        which = spec.get("challenge")
        if which not in CHALLENGES:
            raise GameError(f"challenge: one of {', '.join(CHALLENGES)}")
        duration = _int(spec.get("duration_s", 300), "duration_s", 10,
                        self.arena.settings.max_challenge_duration_s)
        challenge = make_challenge(which, seed)
        # Challenges are for people: bots trade, and would only collect zeros here.
        players = [p for p in players if p["kind"] == "human"]
        config = {"duration_s": duration, "challenge": which, "title": challenge.title}
        rnd = self.arena.store.add_round(self.game["id"], "challenge", None, seed, config)
        runner = ChallengeRound(round_id=rnd["id"], challenge=challenge,
                                player_ids=[p["id"] for p in players], duration_s=duration,
                                on_end=lambda scores: self._round_ended(
                                    rnd, scores, {"solution": challenge.solution()}))
        self.round, self.runner = rnd, runner
        await runner.start()
        await self.broadcast({"type": "round_started", "round": round_info(rnd)})
        for pid in list(self.conns):
            await self.send(pid, runner.public(pid))
        await self.broadcast_info()
        return round_info(rnd)

    async def _watchers(self, msg: dict[str, Any]) -> None:
        runner = self.runner
        for pid in list(self.conns):
            if runner is not None and pid not in runner.seats:
                await self.send(pid, msg)

    async def _lockstep_progress(self, progress: dict[str, Any]) -> None:
        if self.round:
            await self.broadcast({"type": "lockstep_progress", "round": self.round["id"],
                                  **progress})

    async def abort(self) -> None:
        """The server is stopping: a round under way ends without results, as failed."""
        if self._cancel_ready_check():
            return
        runner, rnd = self.runner, self.round
        if runner is None or rnd is None:
            return
        await runner.abort()
        self.arena.store.finish_round(rnd["id"], "failed", {})
        self.round = self.runner = None

    async def stop_round(self, host: dict[str, Any]) -> None:
        if not host["is_host"]:
            raise GameError("only the game's host can end a round", 403)
        async with self.lock:  # never while a round is still starting
            cancelled = self._cancel_ready_check()
            runner = self.runner
        if cancelled:
            await self.broadcast_info()
            return
        if runner is None:
            raise GameError("no round is under way", 409)
        await runner.stop()

    async def _round_ended(self, rnd: dict[str, Any],
                           scores: dict[int, tuple[float, dict[str, Any]]],
                           reveal: dict[str, Any] | None = None, money: bool = False,
                           balances: dict[int, float] | None = None) -> None:
        if money:
            # Each result says what its seat started with and the return on it.
            for pid, (score, details) in scores.items():
                start = (balances or {}).get(pid)
                if start:
                    details["start_balance"] = start
                    details["return_pct"] = round(100 * score / start, 4)
        self.arena.store.finish_round(rnd["id"], "finished", scores, reveal, money=money)
        self.round = self.runner = None
        await self.broadcast({"type": "round_ended", "round": round_info(
            self.arena.store.round(rnd["id"])), "results": self.results(rnd["id"])})
        await self.broadcast({"type": "leaderboard", "boards": self.leaderboard()})
        await self.broadcast_info()

    def leaderboard(self) -> dict[str, Any]:
        store = self.arena.store
        gid = self.game["id"]
        return leaderboards(store.rounds(gid), store.game_results(gid), store.players(gid))

    def results(self, round_id: int) -> list[dict[str, Any]]:
        rows = self.arena.store.results(round_id)
        return [{"rank": i + 1, "player_id": r["player_id"], "name": r["name"], "kind": r["kind"],
                 "score": round(r["score"], 3), "details": r["details"]}
                for i, r in enumerate(rows)]

    async def request(self, player: dict[str, Any], msg: dict[str, Any]) -> None:
        if isinstance(msg, dict) and msg.get("type") == "ready":
            await self.ready(player)
            return
        if self.ready_check is not None:
            raise RequestError("the market opens once everyone has read the briefing")
        runner = self.runner
        if runner is None:
            raise RequestError("no round is under way")
        if isinstance(runner, ChallengeRound):
            if not isinstance(msg, dict) or msg.get("type") != "challenge_answer":
                raise RequestError("a challenge round takes challenge_answer messages only")
            try:
                saved = runner.answer(player["id"], msg.get("answer"))
            except AnswerError as e:
                raise RequestError(str(e)) from None
            await self.send(player["id"], {"type": "challenge_saved", "round": runner.round_id,
                                           "answer": saved})
            return
        if isinstance(runner, LockstepRound):
            if not isinstance(msg, dict) or msg.get("type") != "orders":
                raise RequestError("a lockstep round takes orders messages only")
            why = runner.answer(player["id"], msg)
            if why:
                raise RequestError(why)
            return
        await runner.request(player["id"], msg)


def round_info(r: dict[str, Any], with_briefing: bool = True) -> dict[str, Any]:
    config = r["config"]
    if not with_briefing:
        config = {k: v for k, v in config.items() if k != "briefing"}
    return {"id": r["id"], "number": r["number"], "kind": r["kind"], "market": r["market"],
            "status": r["status"], "config": config, "started_at": r["started_at"],
            "ended_at": r["ended_at"]}


def briefing(kind: MarketKind, seed: int, duration_s: int, news: str | None) -> str:
    """What players are told before a round of this kind; a kind's briefing that fails gives
    way to its description."""
    try:
        return briefing_of(kind, seed, duration_s, news)
    except Exception:
        log.exception("market kind %s gave no briefing", kind.name)
        return kind.description


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise GameError(f"{name}: a whole number from {lo} to {hi}")
    return value


class Arena:
    def __init__(self, settings: Settings, store: Store, kinds: dict[str, MarketKind]):
        self.settings = settings
        self.store = store
        self.kinds = kinds
        self.hubs: dict[int, GameHub] = {}

    def hub(self, game: dict[str, Any]) -> GameHub:
        if game["id"] not in self.hubs:
            self.hubs[game["id"]] = GameHub(self, game)
        return self.hubs[game["id"]]

    def create_game(self, name: Any, host_name: Any,
                    bankroll: Any = None) -> tuple[dict[str, Any], dict[str, Any], str]:
        host_name = check_name(host_name, "your name")
        dollars = 10_000 if bankroll is None else bankroll
        if (isinstance(dollars, bool) or not isinstance(dollars, (int, float))
                or not 100 <= dollars <= 10_000_000):
            raise GameError("bankroll: dollars each player starts with, from 100 to 10,000,000")
        name = check_name(name or f"{host_name[:17]}'s game", "game name")
        for _ in range(20):
            code = new_code()
            if self.store.game_by_code(code) is None:
                break
        else:
            raise GameError("could not find a free code", 500)
        game = self.store.create_game(code, name, round(dollars * money.DOLLAR))
        token = new_token()
        host = self.store.add_player(game["id"], host_name, "human", token_hash(token), True)
        return game, host, token

    def join(self, code: str, name: Any, kind: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        game = self.game(code)
        name = check_name(name)
        if kind not in ("human", "bot"):
            raise GameError("kind is human or bot")
        existing = self.store.players(game["id"])
        if len(existing) >= self.settings.max_players:
            raise GameError("the game is full", 409)
        if any(p["name"].lower() == name.lower() for p in existing):
            raise GameError("that name is taken in this game", 409)
        token = new_token()
        player = self.store.add_player(game["id"], name, kind, token_hash(token))
        return game, player, token

    def game(self, code: Any) -> dict[str, Any]:
        game = self.store.game_by_code(str(code).upper()) if isinstance(code, str) else None
        if game is None:
            raise GameError("no game with that code", 404)
        return game

    def player_for(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str) or not token:
            raise GameError("a player token is needed", 401)
        player = self.store.player_by_token_hash(token_hash(token))
        if player is None:
            raise GameError("unknown player token", 401)
        return player
