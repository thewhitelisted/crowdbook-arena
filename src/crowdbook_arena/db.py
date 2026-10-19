"""Storage: games, players, rounds and results, behind a thin layer over SQLAlchemy Core, so that
SQLite and Postgres are a change of URL. Calls are short and synchronous."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import (JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table,
                        UniqueConstraint, create_engine, select, update)
from sqlalchemy.engine import Engine

metadata = MetaData()

games = Table(
    "games", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(12), nullable=False, unique=True),
    Column("name", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("status", String(16), nullable=False, default="open"),  # open, closed
)

players = Table(
    "players", metadata,
    Column("id", Integer, primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, index=True),
    Column("name", String(32), nullable=False),
    Column("kind", String(8), nullable=False),          # human, bot
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("is_host", Integer, nullable=False, default=0),
    Column("joined_at", Float, nullable=False),
    UniqueConstraint("game_id", "name"),
)

rounds = Table(
    "rounds", metadata,
    Column("id", Integer, primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, index=True),
    Column("number", Integer, nullable=False),
    Column("kind", String(16), nullable=False),          # live, lockstep, challenge
    Column("market", String(64), nullable=True),
    Column("seed", Integer, nullable=False),
    Column("config", JSON, nullable=False),
    Column("status", String(16), nullable=False),        # running, finished, failed
    Column("started_at", Float, nullable=True),
    Column("ended_at", Float, nullable=True),
    UniqueConstraint("game_id", "number"),
)

results = Table(
    "results", metadata,
    Column("id", Integer, primary_key=True),
    Column("round_id", Integer, ForeignKey("rounds.id"), nullable=False, index=True),
    Column("player_id", Integer, ForeignKey("players.id"), nullable=False),
    Column("score", Float, nullable=False),
    Column("details", JSON, nullable=False),
    UniqueConstraint("round_id", "player_id"),
)


class Store:
    def __init__(self, url: str):
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(url, json_serializer=json.dumps)
        metadata.create_all(self.engine)

    # Games and players

    def create_game(self, code: str, name: str) -> dict[str, Any]:
        with self.engine.begin() as c:
            gid = c.execute(games.insert().values(
                code=code, name=name, created_at=time.time(), status="open")).inserted_primary_key[0]
        return self.game(gid)

    def game(self, game_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(games).where(games.c.id == game_id)).mappings().first()
        return dict(row) if row else None

    def game_by_code(self, code: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(games).where(games.c.code == code)).mappings().first()
        return dict(row) if row else None

    def add_player(self, game_id: int, name: str, kind: str, token_hash: str,
                   is_host: bool = False) -> dict[str, Any]:
        with self.engine.begin() as c:
            pid = c.execute(players.insert().values(
                game_id=game_id, name=name, kind=kind, token_hash=token_hash,
                is_host=int(is_host), joined_at=time.time())).inserted_primary_key[0]
        return self.player(pid)

    def player(self, player_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(players).where(players.c.id == player_id)).mappings().first()
        return _player(row) if row else None

    def player_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(players).where(
                players.c.token_hash == token_hash)).mappings().first()
        return _player(row) if row else None

    def players(self, game_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(players).where(players.c.game_id == game_id)
                             .order_by(players.c.id)).mappings().all()
        return [_player(r) for r in rows]

    # Rounds and results

    def add_round(self, game_id: int, kind: str, market: str | None, seed: int,
                  config: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as c:
            number = len(c.execute(select(rounds.c.id).where(
                rounds.c.game_id == game_id)).all()) + 1
            rid = c.execute(rounds.insert().values(
                game_id=game_id, number=number, kind=kind, market=market, seed=seed,
                config=config, status="running", started_at=time.time())).inserted_primary_key[0]
        return self.round(rid)

    def round(self, round_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(rounds).where(rounds.c.id == round_id)).mappings().first()
        return dict(row) if row else None

    def rounds(self, game_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(rounds).where(rounds.c.game_id == game_id)
                             .order_by(rounds.c.number)).mappings().all()
        return [dict(r) for r in rows]

    def finish_round(self, round_id: int, status: str,
                     scores: dict[int, tuple[float, dict[str, Any]]],
                     reveal: dict[str, Any] | None = None) -> None:
        """Stores a round's results; `reveal` is added to its config, such as a challenge's
        solution, which is kept out of sight until the round ends."""
        with self.engine.begin() as c:
            if reveal:
                config = c.execute(select(rounds.c.config).where(
                    rounds.c.id == round_id)).scalar_one()
                c.execute(update(rounds).where(rounds.c.id == round_id).values(
                    config={**config, **reveal}))
            for pid, (score, details) in scores.items():
                c.execute(results.insert().values(round_id=round_id, player_id=pid,
                                                  score=float(score), details=details))
            c.execute(update(rounds).where(rounds.c.id == round_id).values(
                status=status, ended_at=time.time()))

    def results(self, round_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(results, players.c.name, players.c.kind)
                             .join(players, players.c.id == results.c.player_id)
                             .where(results.c.round_id == round_id)
                             .order_by(results.c.score.desc())).mappings().all()
        return [dict(r) for r in rows]

    def abandon_running_rounds(self) -> int:
        """Marks rounds left running by a server that stopped as failed; returns how many."""
        with self.engine.begin() as c:
            return c.execute(update(rounds).where(rounds.c.status == "running").values(
                status="failed", ended_at=time.time())).rowcount


def _player(row: Any) -> dict[str, Any]:
    d = dict(row)
    d.pop("token_hash", None)
    d["is_host"] = bool(d["is_host"])
    return d
