"""The public markets against a yardstick bot (tests/bots/flow_maker.py): following the order flow
must not be a free lunch. It once won 59 rounds in 60; it should now lose a fair share."""

import sys
from pathlib import Path

from arena_kit import run_local

sys.path.insert(0, str(Path(__file__).parent / "bots"))
from flow_maker import MarketMaker  # noqa: E402

SEEDS = range(101, 107)


def test_following_the_flow_is_no_free_lunch():
    wins = {}
    for kind in ("calm", "news", "trending"):
        scores = [run_local(MarketMaker(), kind, seed=s, duration_s=300).score for s in SEEDS]
        wins[kind] = sum(score > 0 for score in scores)
    # It still wins more often than not: a decent idea, but no longer a sure thing anywhere.
    assert all(w < len(SEEDS) for w in wins.values()), wins
    assert sum(wins.values()) <= 13, wins
