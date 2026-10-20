import os
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


PYODIDE_FILES = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip",
                 "pyodide-lock.json")


@pytest.fixture(scope="session")
def pyodide_url():
    """Pyodide for browser bots, from a copy served here: the arena's pinned release, downloaded
    once into a cache, and served with the CORS headers a page's worker needs."""
    import functools
    import http.server

    from crowdbook_arena.config import Settings

    release = Settings.pyodide_url
    version = release.rstrip("/").split("/")[-2]
    cache = Path(os.environ.get("ARENA_TEST_CACHE", Path.home() / ".cache" / "crowdbook-arena"))
    folder = cache / f"pyodide-{version}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in PYODIDE_FILES:
        if not (folder / name).exists():
            r = httpx.get(release + name, timeout=120, follow_redirects=True)
            r.raise_for_status()
            (folder / f"{name}.part").write_bytes(r.content)
            (folder / f"{name}.part").rename(folder / name)

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                          ".wasm": "application/wasm"}

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(folder)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture
def bot_server(tmp_path, pyodide_url):
    s = Server(tmp_path, pyodide_url=pyodide_url)
    yield s
    s.stop()
