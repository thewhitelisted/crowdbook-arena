"""The web server: the JSON API, the WebSocket every screen and bot talks through, and the pages.

    POST /api/games                         {"host_name", "name"?}       -> code, token, player
    POST /api/games/{code}/join             {"name", "kind": human|bot}  -> token, player
    GET  /api/games/{code}                                               -> the game
    POST /api/games/{code}/rounds           (host) {"kind", "market", "duration_s", "speed"}
    POST /api/games/{code}/rounds/stop      (host)
    GET  /api/games/{code}/rounds/{number}                               -> a round and its results
    WS   /ws    first message {"type": "auth", "token"}; then order requests

Host requests carry the host's token as `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import Settings
from .db import Store
from .games import Arena, Connection, GameError, round_info
from .kinds import load_kinds
from .live import RequestError

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_BODY = 16 * 1024
AUTH_TIMEOUT_S = 10.0


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    store = Store(settings.database_url)
    abandoned = store.abandon_running_rounds()
    if abandoned:
        log.warning("%d rounds were left running by the last server and are marked failed",
                    abandoned)
    arena = Arena(settings, store, load_kinds(settings.market_kinds))

    async def body(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise GameError("request too large", 413)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise GameError("the body must be JSON") from None
        if not isinstance(data, dict):
            raise GameError("the body must be a JSON object")
        return data

    def bearer(request: Request) -> dict[str, Any]:
        auth = request.headers.get("authorization", "")
        return arena.player_for(auth.removeprefix("Bearer ").strip() if auth else None)

    def game_and_host(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
        game = arena.game(request.path_params["code"])
        player = bearer(request)
        if player["game_id"] != game["id"]:
            raise GameError("that token is for another game", 403)
        return game, player

    async def create_game(request: Request) -> JSONResponse:
        data = await body(request)
        game, host, token = arena.create_game(data.get("name"), data.get("host_name"))
        return JSONResponse({"code": game["code"], "token": token, "player": host}, 201)

    async def join_game(request: Request) -> JSONResponse:
        data = await body(request)
        game, player, token = arena.join(request.path_params["code"], data.get("name"),
                                         data.get("kind", "human"))
        await arena.hub(game).broadcast_info()
        return JSONResponse({"code": game["code"], "token": token, "player": player}, 201)

    async def get_game(request: Request) -> JSONResponse:
        game = arena.game(request.path_params["code"])
        return JSONResponse(arena.hub(game).info())

    async def start_round(request: Request) -> JSONResponse:
        game, player = game_and_host(request)
        rnd = await arena.hub(game).start_round(player, await body(request))
        return JSONResponse(rnd, 201)

    async def stop_round(request: Request) -> JSONResponse:
        game, player = game_and_host(request)
        await arena.hub(game).stop_round(player)
        return JSONResponse({"stopped": True})

    async def get_round(request: Request) -> JSONResponse:
        game = arena.game(request.path_params["code"])
        number = request.path_params["number"]
        rnd = next((r for r in store.rounds(game["id"]) if r["number"] == number), None)
        if rnd is None:
            raise GameError("no such round", 404)
        hub = arena.hub(game)
        return JSONResponse({"round": round_info(rnd), "results":
                             hub.results(rnd["id"]) if rnd["status"] == "finished" else []})

    async def kinds(_request: Request) -> JSONResponse:
        return JSONResponse([{"name": k.name, "title": k.title, "description": k.description}
                             for k in arena.kinds.values()])

    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_S)
            if not isinstance(first, dict) or first.get("type") != "auth":
                raise GameError("the first message is {\"type\": \"auth\", \"token\": ...}", 401)
            player = arena.player_for(first.get("token"))
        except (GameError, asyncio.TimeoutError, ValueError, KeyError, WebSocketDisconnect) as e:
            message = "no auth message in time" if isinstance(e, asyncio.TimeoutError) else str(e)
            try:
                await ws.send_json({"type": "error", "message": message or "authentication",
                                    "fatal": True})
                await ws.close(code=4401)
            except Exception:
                pass
            return
        game = store.game(player["game_id"])
        hub = arena.hub(game)
        conn = Connection(ws)
        await hub.attach(player, conn)
        try:
            while True:
                try:
                    msg = await ws.receive_json()
                except (ValueError, KeyError):
                    conn.send({"type": "error", "message": "messages are JSON objects",
                               "fatal": False})
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "ping":
                    conn.send({"type": "pong"})
                    continue
                try:
                    await hub.request(player, msg)
                except RequestError as e:
                    conn.send({"type": "error", "message": str(e), "fatal": False,
                               "request": msg if isinstance(msg, dict) else None})
        except WebSocketDisconnect:
            pass
        finally:
            await hub.detach(player["id"], conn)

    async def page(_request: Request) -> FileResponse:
        return FileResponse(STATIC / "index.html")

    async def game_page(_request: Request) -> FileResponse:
        return FileResponse(STATIC / "game.html")

    async def game_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, GameError)
        return JSONResponse({"error": str(exc)}, exc.status)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        yield
        for hub in list(arena.hubs.values()):
            if hub.live is not None:
                await hub.live.stop()

    app = Starlette(
        routes=[
            Route("/", page),
            Route("/g/{code}", game_page),
            Route("/api/kinds", kinds),
            Route("/api/games", create_game, methods=["POST"]),
            Route("/api/games/{code}", get_game),
            Route("/api/games/{code}/join", join_game, methods=["POST"]),
            Route("/api/games/{code}/rounds", start_round, methods=["POST"]),
            Route("/api/games/{code}/rounds/stop", stop_round, methods=["POST"]),
            Route("/api/games/{code}/rounds/{number:int}", get_round),
            WebSocketRoute("/ws", ws_endpoint),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ],
        exception_handlers={GameError: game_error},
        lifespan=lifespan,
    )
    app.state.arena = arena
    return app
