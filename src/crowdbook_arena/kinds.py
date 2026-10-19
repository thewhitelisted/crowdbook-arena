"""Market kinds: the public ones from the kit, and any that installed packages add through the
`crowdbook_arena.market_kinds` entry point group.

An entry point names an object with `name`, `title`, `description` and
`scenario(seed, duration_s) -> str` (arena_kit.MarketKind), or a function returning one or a list
of them. A package installed only on the server, at deploy time, adds kinds that players cannot
read or run offline.
"""

from __future__ import annotations

import logging
import re
from importlib.metadata import entry_points

from arena_kit.markets import PUBLIC_KINDS, MarketKind

GROUP = "crowdbook_arena.market_kinds"
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
log = logging.getLogger(__name__)


def load_kinds(allowed: tuple[str, ...] = ()) -> dict[str, MarketKind]:
    kinds: dict[str, MarketKind] = dict(PUBLIC_KINDS)
    for ep in entry_points(group=GROUP):
        try:
            loaded = ep.load()
            found = loaded() if callable(loaded) and not isinstance(loaded, MarketKind) else loaded
            for kind in found if isinstance(found, (list, tuple)) else [found]:
                if not isinstance(kind, MarketKind) or not _NAME.match(kind.name):
                    raise TypeError(f"{kind!r} is not a market kind")
                if kind.name in kinds:
                    raise ValueError(f"market kind {kind.name!r} is defined twice")
                kinds[kind.name] = kind
        except Exception:
            log.exception("market kinds from entry point %s could not be loaded", ep.name)
    if allowed:
        kinds = {name: kind for name, kind in kinds.items() if name in allowed}
    return kinds
