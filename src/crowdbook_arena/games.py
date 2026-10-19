"""Games in play: who is connected, the round under way, and what everyone is told."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from starlette.websockets import WebSocket

from arena_kit.markets import MarketKind

from .auth import new_code, new_token, token_hash
from .config import Settings
from .db import Store
from .live import LiveRound, RequestError
from .challenges import KINDS as CHALLENGES, AnswerError, ChallengeRound, make as make_challenge
from .lockstep import LockstepRound

log = logging.getLogger(__name__)

NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-.'")
SEND_QUEUE = 2000  # messages a connection may fall behind by before it is dropped


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


class GameHub:
    def __init__(self, arena: "Arena", game: dict[str, Any]):
        self.arena = arena
        self.game = game
        self.conns: dict[int, set[Connection]] = {}
        self.round: dict[str, Any] | None = None
        self.runner: LiveRound | LockstepRound | ChallengeRound | None = None
        self.lock = asyncio.Lock()

    # Connections

    def online(self) -> set[int]:
        return {pid for pid, cs in self.conns.items() if cs}

    async def attach(self, player: dict[str, Any], conn: Connection) -> None:
        self.conns.setdefault(player["id"], set()).add(conn)
        conn.send({"type": "welcome", "player": player, "game": self.info()})
        runner = self.runner
        if isinstance(runner, LiveRound) and self.round:
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
        if not conns and isinstance(self.runner, LiveRound):
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
                    "online": p["id"] in online} for p in store.players(self.game["id"])]
        return {
            "code": self.game["code"], "name": self.game["name"], "players": players,
            "rounds": [round_info(r) for r in store.rounds(self.game["id"])],
            "current": round_info(self.round) if self.round else None,
            "markets": [{"name": k.name, "title": k.title, "description": k.description}
                        for k in self.arena.kinds.values()],
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
            scenario = kind_obj.scenario(seed, duration)
            on_end = lambda scores: self._round_ended(rnd, scores)  # noqa: E731
            try:
                if kind == "live":
                    runner: LiveRound | LockstepRound = LiveRound(
                        round_id=rnd["id"], scenario=scenario, seed=seed, duration_s=duration,
                        speed=config["speed"], player_ids=[p["id"] for p in players],
                        crowdbook=settings.crowdbook, work_dir=settings.work_dir, send=self.send,
                        on_end=on_end, rate_limit=settings.rate_limit,
                        feed_interval_ms=settings.feed_interval_ms, watch=self._watchers)
                else:
                    runner = LockstepRound(
                        round_id=rnd["id"], scenario=scenario, seed=seed,
                        player_ids=[p["id"] for p in players],
                        timeout_ms=config["turn_timeout_ms"], send=self.send,
                        connected=lambda pid: bool(self.conns.get(pid)),
                        on_progress=self._lockstep_progress, on_end=on_end)
                self.round, self.runner = rnd, runner
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
        if isinstance(runner, LiveRound):
            for p in players:
                await self.send(p["id"], {"type": "live_snapshot", "round": rnd["id"],
                                          "seated": True, "state": runner.snapshot(p["id"])})
        await self.broadcast_info()
        return round_info(rnd)

    async def _start_challenge(self, spec: dict[str, Any], seed: int,
                               players: list[dict[str, Any]]) -> dict[str, Any]:
        """Starts a challenge round; called with the lock held."""
        which = spec.get("challenge")
        if which not in CHALLENGES:
            raise GameError(f"challenge: one of {', '.join(CHALLENGES)}")
        duration = _int(spec.get("duration_s", 300), "duration_s", 10,
                        self.arena.settings.max_challenge_duration_s)
        challenge = make_challenge(which, seed)
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
            runner = self.runner
        if runner is None:
            raise GameError("no round is under way", 409)
        await runner.stop()

    async def _round_ended(self, rnd: dict[str, Any],
                           scores: dict[int, tuple[float, dict[str, Any]]],
                           reveal: dict[str, Any] | None = None) -> None:
        self.arena.store.finish_round(rnd["id"], "finished", scores, reveal)
        self.round = self.runner = None
        await self.broadcast({"type": "round_ended", "round": round_info(
            self.arena.store.round(rnd["id"])), "results": self.results(rnd["id"])})
        await self.broadcast_info()

    def results(self, round_id: int) -> list[dict[str, Any]]:
        rows = self.arena.store.results(round_id)
        return [{"rank": i + 1, "player_id": r["player_id"], "name": r["name"], "kind": r["kind"],
                 "score": round(r["score"], 3), "details": r["details"]}
                for i, r in enumerate(rows)]

    async def request(self, player: dict[str, Any], msg: dict[str, Any]) -> None:
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


def round_info(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "number": r["number"], "kind": r["kind"], "market": r["market"],
            "status": r["status"], "config": r["config"], "started_at": r["started_at"],
            "ended_at": r["ended_at"]}


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

    def create_game(self, name: Any, host_name: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        host_name = check_name(host_name, "your name")
        name = check_name(name or f"{host_name[:17]}'s game", "game name")
        for _ in range(20):
            code = new_code()
            if self.store.game_by_code(code) is None:
                break
        else:
            raise GameError("could not find a free code", 500)
        game = self.store.create_game(code, name)
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
