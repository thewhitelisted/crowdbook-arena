import asyncio
import json

import websockets

from arena_kit.client import ws_url


async def recv_until(ws, pred, timeout=20):
    async def loop():
        async for raw in ws:
            msg = json.loads(raw)
            if pred(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout)


def is_live(kind):
    return lambda m: m["type"] == "live" and m["m"]["type"] == kind


async def connect(server, token):
    ws = await websockets.connect(ws_url(server.url))
    await ws.send(json.dumps({"type": "auth", "token": token}))
    await recv_until(ws, lambda m: m["type"] == "welcome")
    return ws


async def start_round(server, code, host_token, **spec):
    body = {"kind": "live", "market": "calm", "duration_s": 60, "speed": 1, **spec}
    r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds", body, host_token)
    assert r.status_code == 201, r.text
    return r.json()
