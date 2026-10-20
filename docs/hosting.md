# Hosting

How to put the arena on your own domain with HTTPS. It is all configuration: one server, one DNS
record, and `docker compose up`. Caddy gets the certificate from Let's Encrypt and renews it by
itself. Nothing here costs money except the server and the domain you already have.

## What you need

- **A small Linux server** with a public IP address: 1 vCPU and 1 GB of memory hold a club's
  games. Each live round runs one `crowdbook serve` process, which takes a few percent of a core,
  and lockstep rounds are lighter still. x86-64 and arm64 both work.
- **Docker** with the compose plugin, installed with `curl -fsSL https://get.docker.com | sh` on
  most distributions.
- **Ports 80 and 443** open to the internet. Caddy needs port 80 to get its certificate.
- **A domain or subdomain**, such as `arena.example.com`.

## 1. DNS

At your DNS provider, add an **A record** for the name pointing at the server's IPv4 address, and
an **AAAA record** too if the server has IPv6:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `arena` | `203.0.113.10` | 300 |

Check that it resolves before going on: `dig +short arena.example.com`.

## 2. The server

```bash
git clone https://github.com/thewhitelisted/crowdbook-arena && cd crowdbook-arena
cp .env.example .env
nano .env                 # ARENA_DOMAIN=arena.example.com
docker compose up -d --build
```

Open `https://arena.example.com`. The first visit can take a few seconds while Caddy gets the
certificate. `docker compose logs -f` shows what the arena and Caddy are doing.

Bots connect with `arena-kit join bot.py --server https://arena.example.com --code ...`. WebSockets
go through Caddy with nothing to configure.

## Updating

```bash
git pull && docker compose up -d --build
```

A round under way when the arena stops is ended without results and marked failed, and a
challenge still open is closed without revealing anything. Update between rounds, when no
challenge is open.

## Postgres instead of SQLite

SQLite, on the `arena-data` volume, is plenty for a club. For Postgres, set these in `.env`:

```
POSTGRES_PASSWORD=a-long-random-password
ARENA_DATABASE_URL=postgresql+psycopg://arena:a-long-random-password@postgres/arena
```

and start with the profile:

```bash
docker compose --profile postgres up -d --build
```

The arena creates its tables at startup.

## Backups

With SQLite, everything is on the `arena-data` volume: the database and each round's recorded
session. To back it up:

```bash
docker compose exec arena python -c "import sqlite3; s=sqlite3.connect('/data/arena.db'); s.backup(sqlite3.connect('/data/backup.db'))"
docker compose cp arena:/data/backup.db ./arena-backup.db
```

With Postgres, use `docker compose exec postgres pg_dump -U arena arena > arena.sql`.

## Private market kinds

Market kinds can come from a package installed only on the server, through the
`crowdbook_arena.market_kinds` entry point group. To add one:

1. Build that package's wheel.
2. Put the wheel in `plugins/`, which git ignores apart from its placeholder.
3. Rebuild with `docker compose up -d --build`.

A kind can have a `briefing(seed, duration_s)` method returning what players are told before a
round; without one, players get the kind's description. Keep the round's path out of it. A kind
with several linked assets subclasses `arena_kit.markets.LinkedKind` (as `PairKind` and
`BasketKind` do): its `values(seed, duration_s)` draws every asset's fair value.

The arena logs the kinds it loads. `ARENA_MARKET_KINDS` in `.env` limits which kinds games may use.
The wheel never enters this repository.

## Settings

Every `ARENA_*` setting in the [README](../README.md#settings) can go in `.env`.
`ARENA_HOST` and `ARENA_PORT` are fixed by the compose file, since Caddy reaches the arena on the
compose network and only Caddy's ports are published.
