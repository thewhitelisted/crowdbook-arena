"""Bots in a browser tab: the Python side of a bot that a player pastes into the game page.

The page runs Pyodide in a Web Worker, writes this package's pure-Python modules into it (the
ones in PACKAGE_FILES, with PACKAGE_INIT as the package's __init__), and makes a `Runner` with
the player's bot file. The worker keeps the bot's WebSocket and hands each message from the
server to `Runner.handle`; the runner plays it through the same `Session` as `arena-kit join`,
and reports what the bot does, for the page to show. The bot's code runs only in the player's
tab, never on the server.
"""

from __future__ import annotations

import json
import logging
import traceback
import types
from typing import Any, Callable

from .bot import Bot, Portfolio, Quote, State, bot_from_module
from .client import Session

PACKAGE_FILES = ("bot.py", "money.py", "tracker.py", "client.py", "browser.py")
PACKAGE_INIT = ('"""crowdbook-arena\'s kit, in a browser tab."""\n\n'
                "from .bot import Bot, Fill, News, Quote, State, Trade\n")
FILLS_KEPT = 50  # the latest fills a report carries


class Watched(Bot):
    """A bot that tells the page what it did at each turn."""

    def __init__(self, bot: Bot, report: Callable[..., None]):
        self.bot = bot
        self.report = report
        self.round: Any = None
        self.mode: Any = None
        self.fills: list[list[Any]] = []

    def on_start(self, info: dict[str, Any]) -> None:
        self.round, self.mode, self.fills = info.get("round"), info.get("mode"), []
        self.report("started", round=self.round, mode=self.mode,
                    market=info.get("market"))
        self.bot.on_start(info)

    def on_turn(self, state: State | Portfolio) -> Any:
        if isinstance(state, Portfolio):
            return self._portfolio_turn(state)
        self.fills.extend([f.time, f.side, f.price, f.quantity] for f in state.fills)
        del self.fills[:-FILLS_KEPT]
        quote = self.bot.on_turn(state)
        self.report("turn", round=self.round, mode=self.mode, time=state.time,
                    time_left=state.time_left, position=state.position,
                    pnl=state.equity - state.balance, fees=state.fees,
                    resting={"bid": state.my_bid, "ask": state.my_ask},
                    quote=quote.to_json() if isinstance(quote, Quote) else None,
                    fills=self.fills)
        return quote

    def _portfolio_turn(self, p: Portfolio) -> Any:
        for sym, a in p.assets.items():
            self.fills.extend([f.time, f.side, f.price, f.quantity, sym] for f in a.fills)
        self.fills.sort(key=lambda f: f[0])
        del self.fills[:-FILLS_KEPT]
        answer = self.bot.on_turn(p)
        self.report("turn", round=self.round, mode=self.mode, time=p.time,
                    time_left=p.time_left,
                    position=sum(a.position for a in p.assets.values()),
                    positions={s: a.position for s, a in p.assets.items()},
                    pnl=p.equity - p.balance, fees=sum(a.fees for a in p.assets.values()),
                    resting={s: {"bid": a.my_bid, "ask": a.my_ask} for s, a in p.assets.items()},
                    fills=self.fills)
        return answer

    def on_end(self, result: dict[str, Any]) -> None:
        mine = result.get("result") or {}
        self.report("ended", round=self.round, score=mine.get("score"), rank=mine.get("rank"),
                    of=len(result.get("results", [])))
        self.bot.on_end(result)


class _Errors(logging.Handler):
    """Passes the kit's warnings and errors on to the page, with their tracebacks."""

    def __init__(self, report: Callable[..., None]):
        super().__init__(logging.WARNING)
        self.report = report

    def emit(self, record: logging.LogRecord) -> None:
        trace = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
        self.report("error" if record.levelno >= logging.ERROR else "warning",
                    message=record.getMessage(), trace=trace)


def load(source: str) -> Bot:
    """The bot a bot file's text defines, as `arena-kit join` loads it from a file."""
    module = types.ModuleType("arena_bot")
    module.__file__ = "bot.py"
    exec(compile(source, "bot.py", "exec"), module.__dict__)
    return bot_from_module(module)


class Runner:
    """One bot, played from its own seat. `send` takes the text of each message for the server,
    `report` the text of each report for the page (JSON, with a "type")."""

    def __init__(self, source: str, send: Callable[[str], Any], report: Callable[[str], Any]):
        self._report = report
        self.bot = Watched(load(source), self.report)
        handler = _Errors(self.report)
        kit = logging.getLogger("arena_kit")
        for old in [h for h in kit.handlers if isinstance(h, _Errors)]:
            kit.removeHandler(old)
        kit.addHandler(handler)

        async def to_server(msg: dict[str, Any]) -> None:
            send(json.dumps(msg))

        self.session = Session(self.bot, to_server)

    def report(self, kind: str, **data: Any) -> None:
        self._report(json.dumps({"type": kind, **data}))

    async def handle(self, text: str) -> None:
        """Acts on one message from the server."""
        try:
            await self.session.handle(json.loads(text))
        except ConnectionError as e:
            self.report("refused", message=str(e))
        except Exception as e:
            self.report("error", message=f"{type(e).__name__}: {e}",
                        trace=traceback.format_exc())


def start(source: str, send: Callable[[str], Any], report: Callable[[str], Any]) -> Runner | None:
    """A runner for the bot file's text, or None, after reporting why, if it cannot be played:
    a syntax error, an import that fails, an exception while it loads, or no bot in it."""
    try:
        return Runner(source, send, report)
    except SyntaxError as e:
        message = f"syntax error on line {e.lineno}: {e.msg}"
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
    report(json.dumps({"type": "error", "message": f"the bot did not load: {message}",
                       "trace": traceback.format_exc()}))
    return None
