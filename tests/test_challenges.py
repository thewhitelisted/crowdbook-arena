"""Manual challenges: exact values, answers checked, and scores hidden until the close."""

import asyncio
import json
import math

import pytest

from crowdbook_arena.challenges import AnswerError, ChallengeRound, make
from helpers import connect, recv_until


@pytest.mark.parametrize("kind", ["auction", "conversion", "kelly"])
def test_best_is_100_nothing_is_0_and_nothing_beats_best(kind):
    for seed in range(40):
        c = make(kind, seed)
        assert c.score(c.best()) == pytest.approx(100)
        assert c.score(c.nothing()) == 0
        assert make(kind, seed).public() == c.public()  # the same seed, the same challenge
    c = make(kind, 1)
    if kind == "auction":
        values = [c.value({"bid": b}) for b in range(0, 3 * c.worth)]
        assert max(values) == pytest.approx(c.value(c.best()))
    elif kind == "kelly":
        grid = [c.value({"fraction": f / 1000}) for f in range(1000)]
        assert max(grid) <= c.value(c.best()) + 1e-9
        assert c.score({"fraction": 1}) == -100


def test_auction_and_kelly_values_by_hand():
    c = make("auction", 3)
    c.worth, c.rivals, c.lo, c.hi = 100, 2, 50, 99  # 50 equally likely bids
    assert c.value({"bid": 75}) == pytest.approx(25 * (25 / 50) ** 2)
    assert c.value({"bid": 50}) == 0  # every rival bids at least 50, and ties lose
    k = make("kelly", 3)
    k.p, k.odds = 0.6, 1.0
    assert k.best() == {"fraction": pytest.approx(0.2)}
    assert k.value({"fraction": 0.2}) == pytest.approx(0.6 * math.log(1.2) + 0.4 * math.log(0.8))


def test_conversion_paths():
    c = make("conversion", 5)
    assert c.value({"path": []}) == c.START
    best = c.best()["path"]
    assert best[-1] == "Shells" and 2 <= len(best) <= 5
    with pytest.raises(AnswerError):
        make("auction", 1).check({"bid": 10**400})
    with pytest.raises(AnswerError):
        make("kelly", 1).check({"fraction": float("nan")})
    for bad in ({"path": ["Pearls"]}, {"path": ["Shells"]}, {"path": ["Gold", "Shells"]},
                {"path": ["Pearls", "Pearls", "Shells"]}, {"path": ["Pearls"] * 6},
                {"path": "Shells"}, {"bid": 1}, None):
        with pytest.raises(AnswerError):
            c.check(bad)


def test_a_challenge_closes_on_its_own():
    ended = []

    async def go():
        async def on_end(scores):
            ended.append(scores)
        r = ChallengeRound(round_id=1, challenge=make("kelly", 1), player_ids=[1, 2],
                           duration_s=0.2, on_end=on_end)
        await r.start()
        r.answer(1, {"fraction": 0.05})
        await asyncio.wait_for(r.wait(), 5)
        with pytest.raises(AnswerError):
            r.answer(2, {"fraction": 0.05})
    asyncio.run(go())
    assert ended[0][2] == (0.0, {"answered": False})
    assert ended[0][1][1]["answered"]


def test_a_challenge_round_end_to_end(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")
    carol = server.join(code, "carol")
    seen = []

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        c = await connect(server, carol["token"])
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "challenge", "challenge": "auction", "seed": 9,
                                     "duration_s": 600}, game["token"])
        assert r.status_code == 201, r.text
        prompt = await recv_until(b, lambda m: m["type"] == "challenge")
        seen.append(prompt)
        best = make("auction", 9).best()
        await b.send(json.dumps({"type": "challenge_answer", "answer": {"bid": 1}}))
        seen.append(await recv_until(b, lambda m: m["type"] == "challenge_saved"))
        await b.send(json.dumps({"type": "challenge_answer", "answer": best}))  # changes it
        seen.append(await recv_until(b, lambda m: m["type"] == "challenge_saved"))
        await a.send(json.dumps({"type": "challenge_answer", "answer": {"bid": -3}}))
        err = await recv_until(a, lambda m: m["type"] == "error")
        assert "bid" in err["message"]
        await a.send(json.dumps({"type": "challenge_answer", "answer": {"bid": 5}}))
        await recv_until(a, lambda m: m["type"] == "challenge_saved")
        # Reconnecting shows the challenge again, with your answer and no score.
        await b.close()
        b = await connect(server, bob["token"])
        again = await recv_until(b, lambda m: m["type"] == "challenge")
        assert again["answer"] == best
        seen.append(again)
        info = server.http.get(f"/api/games/{code}").json()
        seen.append(info)
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        ended = await recv_until(c, lambda m: m["type"] == "round_ended")
        for ws in (a, b, c):
            await ws.close()
        return ended

    ended = asyncio.run(go())
    # Nothing a player could see before the close gave away a score or the solution.
    for msg in seen:
        text = json.dumps(msg)
        assert '"score"' not in text and '"solution"' not in text, text
    rows = {r["name"]: r for r in ended["results"]}
    assert rows["bob"]["score"] == pytest.approx(100) and rows["bob"]["rank"] == 1
    assert rows["carol"]["score"] == 0 and rows["carol"]["details"]["answered"] is False
    assert rows["alice"]["score"] == 0  # a bid of 5 never wins, so it is worth nothing
    sol = ended["round"]["config"]["solution"]
    assert sol["answer"] == make("auction", 9).best()
    stored = server.http.get(f"/api/games/{code}/rounds/1").json()
    assert stored["round"]["config"]["solution"] == sol


def test_stopping_the_server_fails_an_open_challenge_without_revealing_it(tmp_path):
    from conftest import Server
    from crowdbook_arena.db import Store

    s = Server(tmp_path)
    game = s.new_game("alice")
    r = s.post(f"/api/games/{game['code']}/rounds", {"kind": "challenge", "challenge": "kelly",
                                                      "duration_s": 3600}, game["token"])
    assert r.status_code == 201
    s.stop()
    store = Store(f"sqlite:///{tmp_path}/arena.db")
    rnd = store.rounds(store.game_by_code(game["code"])["id"])[0]
    assert rnd["status"] == "failed" and "solution" not in rnd["config"]
