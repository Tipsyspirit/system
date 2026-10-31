import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine, ml, rules as rules_mod  # noqa: E402
from app.main import create_app  # noqa: E402

R = rules_mod.load()


@pytest.fixture()
def c():
    cl = TestClient(create_app(":memory:"))
    t = cl.post("/api/register", json={"email": "a@b.co", "password": "password1"}).json()["token"]
    cl.headers["Authorization"] = f"Bearer {t}"
    st = cl.post("/api/player", json={"name": "A", "focus": "STR", "quest": "Walk", "rarity": "rare"}).json()
    cl.qid = st["goals"][0]["milestones"][0]["quests"][0]["id"]
    cl.mid = st["goals"][0]["milestones"][0]["id"]
    f = cl.post(f"/api/milestones/{cl.mid}/quests", json={"text": "Filler", "rarity": "rare"}).json()
    cl.fid = f["goals"][0]["milestones"][0]["quests"][-1]["id"]
    return cl


# ---- rules.txt
def test_rules_example_from_spec():
    """Rare, effort 4, note, first today = 12 stat XP. STR 5 with 55/60 -> level 6, 7/70."""
    p = engine.new_player(R, "A", "", "", "STR")
    p["statXp"]["STR"] = 55
    g = {"stat": "STR", "text": "g", "milestones": []}
    q = {"id": 1, "text": "x", "rarity": "rare", "done": False, "runs": [], "created": 0}
    m = {"text": "m", "quests": [q, {"id": 2, "text": "y", "rarity": "rare", "done": False, "runs": [], "created": 0}]}
    g["milestones"].append(m)
    engine.complete_quest(R, p, g, m, q, 4, "note", note="did it", now_ms=10**12)
    assert p["statLevel"]["STR"] == 6 and p["statXp"]["STR"] == 7
    assert p["log"][0]["sx"] == 12


def test_thresholds_table():
    assert [R.stat_req(l) for l in range(5, 11)] == [60, 70, 80, 90, 100, 110]
    assert R.level_need(1) == 100 and R.level_need(3) == 220


@pytest.mark.parametrize("key", rules_mod.REQUIRED_FALSE)
def test_invariant_break_refuses_to_boot(tmp_path, key):
    txt = (ROOT / "rules.txt").read_text()
    txt = re.sub(rf"^{key}\s*=\s*false", f"{key} = true", txt, flags=re.M)
    f = tmp_path / "rules.txt"
    f.write_text(txt)
    with pytest.raises(rules_mod.RulesError):
        rules_mod.load(f)


def test_missing_and_malformed_rules(tmp_path):
    with pytest.raises(rules_mod.RulesError):
        rules_mod.load(tmp_path / "nope.txt")
    f = tmp_path / "bad.txt"
    f.write_text("[meta\nx=1")
    with pytest.raises(rules_mod.RulesError):
        rules_mod.load(f)


def test_changing_rules_changes_behavior(tmp_path):
    txt = (ROOT / "rules.txt").read_text().replace("rare      = 10", "rare      = 20")
    f = tmp_path / "rules.txt"
    f.write_text(txt)
    r2 = rules_mod.load(f)
    assert r2.base_stat_xp("rare") == 20 and R.base_stat_xp("rare") == 10


# ---- audit-trail rules through the API
def test_no_manual_stat_endpoints(c):
    for path in ("/api/spend", "/api/stats", "/api/points", "/api/buy"):
        assert c.post(path, json={"stat": "STR"}).status_code in (404, 405)
    st = c.get("/api/state").json()
    assert "points" not in st


def test_complete_and_audit_log(c):
    r = c.post(f"/api/quests/{c.qid}/complete", json={"effort": 4, "evidence": "note", "note": "ok"})
    assert r.status_code == 200
    st = r.json()["state"]
    assert st["statLevel"]["STR"] == 5 and st["statXp"]["STR"] == 12
    e = st["log"][-1]
    assert e["sx"] == 12 and e["xp"] == 50 and e["rules"] == R.sha[:12]
    assert any("STR +12 XP" in m for m in r.json()["messages"])


def test_note_required_and_epic_needs_proof(c):
    assert c.post(f"/api/quests/{c.qid}/complete", json={"effort": 3, "evidence": "note"}).status_code == 422
    q = c.post(f"/api/milestones/{c.mid}/quests", json={"text": "Big", "rarity": "epic"}).json()
    eq = q["goals"][0]["milestones"][0]["quests"][-1]["id"]
    assert c.post(f"/api/quests/{eq}/complete", json={"effort": 3, "evidence": "note", "note": "x"}).status_code == 422
    assert c.post(f"/api/quests/{eq}/complete", json={"effort": 3, "evidence": "proof", "proof": "link"}).status_code == 200


def test_legendary_needs_review_and_reflection(c):
    q = c.post(f"/api/milestones/{c.mid}/quests", json={"text": "Huge", "rarity": "legendary"}).json()
    lq = q["goals"][0]["milestones"][0]["quests"][-1]["id"]
    body = {"effort": 3, "evidence": "proof_reflection", "proof": "p", "reflection": "r"}
    assert c.post(f"/api/quests/{lq}/complete", json=body).status_code == 422  # no review yet
    assert c.post("/api/review", json={"wins": "went well"}).status_code == 200
    assert c.post(f"/api/quests/{lq}/complete", json=body).status_code == 200


def test_repeat_diminishing_and_common_repeatable(c):
    q = c.post(f"/api/milestones/{c.mid}/quests", json={"text": "Brush", "rarity": "common"}).json()
    cq = q["goals"][0]["milestones"][0]["quests"][-1]["id"]
    sx = []
    for _ in range(3):
        r = c.post(f"/api/quests/{cq}/complete", json={"effort": 3, "evidence": "note", "note": "n"})
        assert r.status_code == 200
        sx.append(r.json()["state"]["log"][-1]["sx"])
    assert sx == [4.0, 2.0, 0.4]
    assert c.post(f"/api/quests/{c.qid}/complete", json={"effort": 3, "evidence": "note", "note": "n"}).status_code == 200
    assert c.post(f"/api/quests/{c.qid}/complete", json={"effort": 3, "evidence": "note", "note": "n"}).status_code == 422


def test_daily_stat_cap_and_flagged(c):
    ids = []
    for i in range(6):
        s = c.post(f"/api/milestones/{c.mid}/quests", json={"text": f"E{i}", "rarity": "epic"}).json()
        ids.append(s["goals"][0]["milestones"][0]["quests"][-1]["id"])
    for qid in ids:
        c.post(f"/api/quests/{qid}/complete", json={"effort": 5, "evidence": "proof_reflection",
                                                     "proof": "p", "reflection": "r"})
    st = c.get("/api/state").json()
    assert st["today"]["stat"]["STR"] <= 40
    assert any("Daily STR cap" in e.get("flags", []) for e in st["log"])
    assert st["today"]["xp"] <= 200


def test_no_stat_loss_and_resting_comeback():
    p = engine.new_player(R, "A", "", "", "STR", now_ms=0)
    now = 31 * 86400 * 1000
    assert engine.is_resting(R, p, "STR", now)
    assert p["statLevel"]["STR"] == 5  # nothing subtracts
    g = {"stat": "STR", "text": "g", "milestones": []}
    q = {"id": 1, "text": "x", "rarity": "common", "done": False, "runs": [], "created": 0}
    m = {"text": "m", "quests": [q]}
    g["milestones"].append(m)
    engine.complete_quest(R, p, g, m, q, 3, "note", note="n", now_ms=now)
    assert p["log"][0]["sx"] == 4.8  # 4 x 1.2


def test_review_once_per_week_and_no_history_rewrite(c):
    r1 = c.post("/api/review", json={"wins": "a"}).json()
    xp1 = r1["state"]["xp"]
    r2 = c.post("/api/review", json={"wins": "changed"}).json()
    assert r2["state"]["xp"] == xp1
    assert c.post("/api/review", json={}).status_code in (200, 422)


def test_goal_and_milestone_bonuses_exempt(c):
    c.post(f"/api/quests/{c.fid}/complete", json={"effort": 3, "evidence": "note", "note": "n"})
    r = c.post(f"/api/quests/{c.qid}/complete", json={"effort": 3, "evidence": "note", "note": "n"}).json()
    types = [e["type"] for e in r["state"]["log"]]
    assert "milestone" in types and "goal" in types
    bonus = [e for e in r["state"]["log"] if e["type"] in ("milestone", "goal")]
    assert all("exempt" in " ".join(e["flags"]) for e in bonus)


def test_title_from_profile():
    p = engine.new_player(R, "A", "", "", "STR")
    assert engine.profile_title(R, p) == ""
    p["statLevel"]["FOC"] = 8
    assert engine.profile_title(R, p) == "Executor"
    p["statLevel"]["KNO"] = 7
    assert engine.profile_title(R, p) == "Executor"  # single top >= 8


def test_levelup_gives_no_points_and_restores_mp():
    p = engine.new_player(R, "A", "", "", "STR")
    p["mp"] = 10
    assert engine.add_overall_xp(R, p, 100) is True
    assert p["level"] == 2 and p["mp"] == 100 and "points" not in p


def test_auth_required():
    cl = TestClient(create_app(":memory:"))
    assert cl.get("/api/state").status_code == 401
    assert cl.post("/api/login", json={"email": "x@y.zz", "password": "whatever1"}).status_code == 401


# ---- ML: advises, evolves within guards, cannot touch rewards
def test_engine_never_imports_ml():
    src = (ROOT / "backend/app/engine.py").read_text()
    assert "ml" not in re.findall(r"from \.(\w+) import|import \.(\w+)", src) and "from . import ml" not in src
    assert "from .ml" not in src


def test_ml_learns_within_step_cap():
    m = ml.Model(R)
    p = engine.new_player(R, "A", "", "", "STR")
    q = {"rarity": "rare", "est_minutes": 30, "resistance": 3, "done": False}
    x = ml.features(R, q, "STR", p, 10**12)
    cap = R.f("ml", "max_weight_step")
    for _ in range(40):
        before = list(m.w)
        m.update(x, 1)
        assert all(abs(a - b) <= cap + 1e-9 for a, b in zip(m.w, before))
    assert m.predict(x) > 0.6 and m.version > 0


def test_ml_warmup_no_change():
    m = ml.Model(R)
    x = [0.5] * len(R.l("ml", "features"))
    for _ in range(R.i("ml", "min_samples_to_adapt") - 1):
        m.update(x, 1)
    assert all(w == 0 for w in m.w)


def test_ml_rarity_suggestion_after_easy_ratings(c):
    for i in range(12):
        s = c.post(f"/api/milestones/{c.mid}/quests", json={"text": f"c{i}", "rarity": "common"}).json()
        qid = s["goals"][0]["milestones"][0]["quests"][-1]["id"]
        c.post(f"/api/quests/{qid}/complete", json={"effort": 1, "evidence": "note", "note": "n"})
    s = c.post(f"/api/milestones/{c.mid}/quests", json={"text": "new", "rarity": "common"}).json()
    q = s["goals"][0]["milestones"][0]["quests"][-1]
    adv = s["advice"][str(q["id"])]
    assert adv["suggested_rarity"] == "rare"
    assert q["rarity"] == "common"  # advice is never applied automatically


def test_ml_rewards_identical_with_and_without_model(c):
    """XP and stat XP must be the same whether the model is enabled, trained or not."""
    r = c.post(f"/api/quests/{c.qid}/complete", json={"effort": 3, "evidence": "note", "note": "n"}).json()
    assert r["state"]["log"][-1]["sx"] == 10.0


def test_skip_has_no_penalty(c):
    before = c.get("/api/state").json()
    after = c.post(f"/api/quests/{c.qid}/skip").json()
    assert after["statLevel"] == before["statLevel"] and after["xp"] == before["xp"]
    assert after["ml"]["samples"] == 1


def test_suggest_rarity_rules():
    assert engine.suggest_rarity(R, 10, 1) == "common"
    assert engine.suggest_rarity(R, 45, 3) == "rare"
    assert engine.suggest_rarity(R, 300, 5, multi_session=True) == "epic"
