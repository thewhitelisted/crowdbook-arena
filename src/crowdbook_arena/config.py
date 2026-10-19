"""The server's settings, all from environment variables so that hosting is configuration."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    # Where games, players and results are kept: any SQLAlchemy URL. SQLite unless given;
    # postgresql+psycopg://user:password@host/db for Postgres.
    database_url: str = "sqlite:///data/arena.db"
    host: str = "127.0.0.1"
    port: int = 8000
    # The crowdbook command that serves live rounds; the one beside this Python's, else PATH's.
    crowdbook: str = ""
    # Where live rounds keep their scenario, token and session files.
    work_dir: str = "data/rounds"
    # Market kinds games may use, by name; empty for every kind found.
    market_kinds: tuple[str, ...] = ()
    max_players: int = 32
    max_live_duration_s: int = 3600
    rate_limit: int = 50           # gateway messages a seat may send a second
    feed_interval_ms: int = 100    # market data batched this often to every seat
    lockstep_timeout_ms: int = 2000   # how long a bot has to answer a turn, unless a round says
    max_lockstep_duration_s: int = 7200
    max_challenge_duration_s: int = 7 * 24 * 3600

    @classmethod
    def from_env(cls) -> "Settings":
        kinds = os.environ.get("ARENA_MARKET_KINDS", "")
        return cls(
            database_url=os.environ.get("ARENA_DATABASE_URL", cls.database_url),
            host=os.environ.get("ARENA_HOST", cls.host),
            port=_int("ARENA_PORT", cls.port),
            crowdbook=os.environ.get("ARENA_CROWDBOOK", "") or find_crowdbook(),
            work_dir=os.environ.get("ARENA_WORK_DIR", cls.work_dir),
            market_kinds=tuple(k.strip() for k in kinds.split(",") if k.strip()),
            max_players=_int("ARENA_MAX_PLAYERS", cls.max_players),
            max_live_duration_s=_int("ARENA_MAX_LIVE_DURATION_S", cls.max_live_duration_s),
            rate_limit=_int("ARENA_RATE_LIMIT", cls.rate_limit),
            feed_interval_ms=_int("ARENA_FEED_INTERVAL_MS", cls.feed_interval_ms),
            lockstep_timeout_ms=_int("ARENA_LOCKSTEP_TIMEOUT_MS", cls.lockstep_timeout_ms),
            max_lockstep_duration_s=_int("ARENA_MAX_LOCKSTEP_DURATION_S",
                                         cls.max_lockstep_duration_s),
            max_challenge_duration_s=_int("ARENA_MAX_CHALLENGE_DURATION_S",
                                          cls.max_challenge_duration_s),
        )


def find_crowdbook() -> str:
    import sys

    beside = Path(sys.executable).parent / "crowdbook"
    if beside.exists():
        return str(beside)
    return shutil.which("crowdbook") or "crowdbook"
