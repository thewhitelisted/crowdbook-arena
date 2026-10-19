import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from crowdbook_arena.app import create_app
from crowdbook_arena.config import Settings, find_crowdbook


class Server:
    def __init__(self, tmp: Path, **overrides):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        settings = Settings(database_url=f"sqlite:///{tmp}/arena.db", crowdbook=find_crowdbook(),
                            work_dir=str(tmp / "rounds"), **overrides)
        self.app = create_app(settings)
        self.uvicorn = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                     log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.uvicorn.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        self.http = httpx.Client(base_url=self.url, timeout=20)

    def post(self, path, json=None, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.http.post(path, json=json or {}, headers=headers)

    def new_game(self, host="host"):
        r = self.post("/api/games", {"host_name": host})
        assert r.status_code == 201, r.text
        return r.json()

    def join(self, code, name, kind="human"):
        r = self.post(f"/api/games/{code}/join", {"name": name, "kind": kind})
        assert r.status_code == 201, r.text
        return r.json()

    def stop(self):
        self.uvicorn.should_exit = True
        self.thread.join(15)


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()
