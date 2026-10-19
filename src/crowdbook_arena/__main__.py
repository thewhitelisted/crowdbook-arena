"""`crowdbook-arena`: runs the server with the settings in the environment (config.py)."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(prog="crowdbook-arena", description=__doc__)
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = create_app(settings)
    print(f"crowdbook-arena on http://{args.host}:{args.port}  (database {settings.database_url})")
    print(f"market kinds: {', '.join(app.state.arena.kinds)}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                ws_max_size=64 * 1024, proxy_headers=True)


if __name__ == "__main__":
    main()
