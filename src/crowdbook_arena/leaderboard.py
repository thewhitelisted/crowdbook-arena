"""Leaderboards across a game's rounds: one for each kind of round, and one over all of them.

A market round's scores are in tick-lots, which differ from market to market, so each counts by
rank: a round of n players gives the winner 100 points, the last 0 and the rest evenly between,
ties sharing. A challenge's score is already on a fixed scale (0 for doing nothing, 100 for the
best answer) and counts as it is. Every board adds up a player's points over its rounds.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

BOARDS = {"live": "Live trading", "lockstep": "Algorithmic", "challenge": "Challenges",
          "overall": "Overall"}


def rank_points(scores: dict[int, float]) -> dict[int, float]:
    """100 for the best score down to 0 for the worst, ties sharing the points of their places."""
    n = len(scores)
    if n == 1:
        return {pid: 100.0 for pid in scores}
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    points: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        share = sum(100 * (n - 1 - k) / (n - 1) for k in range(i, j + 1)) / (j - i + 1)
        for k in range(i, j + 1):
            points[ordered[k][0]] = share
        i = j + 1
    return points


def leaderboards(rounds: list[dict[str, Any]], results: list[dict[str, Any]],
                 players: list[dict[str, Any]]) -> dict[str, Any]:
    """`rounds` the game's finished rounds, `results` their rows (round_id, player_id, score)."""
    by_round: dict[int, dict[int, float]] = defaultdict(dict)
    for r in results:
        by_round[r["round_id"]][r["player_id"]] = r["score"]
    totals: dict[str, dict[int, float]] = {b: defaultdict(float) for b in BOARDS}
    counts: dict[str, dict[int, int]] = {b: defaultdict(int) for b in BOARDS}
    for rnd in rounds:
        scores = by_round.get(rnd["id"])
        if rnd["status"] != "finished" or not scores:
            continue
        points = dict(scores) if rnd["kind"] == "challenge" else rank_points(scores)
        for pid, pts in points.items():
            for board in (rnd["kind"], "overall"):
                totals[board][pid] += pts
                counts[board][pid] += 1
    names = {p["id"]: p for p in players}
    out = {}
    for board, title in BOARDS.items():
        rows = [{"player_id": pid, "name": names[pid]["name"], "kind": names[pid]["kind"],
                 "points": round(pts, 2), "rounds": counts[board][pid]}
                for pid, pts in totals[board].items() if pid in names]
        rows.sort(key=lambda r: (-r["points"], r["name"].lower()))
        for i, row in enumerate(rows):
            row["rank"] = i + 1 if i == 0 or row["points"] != rows[i - 1]["points"] \
                else rows[i - 1]["rank"]
        out[board] = {"title": title, "rows": rows}
    return out
