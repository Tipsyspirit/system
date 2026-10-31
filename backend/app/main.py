import hashlib
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import engine, ml
from .db import DB
from .rules import RARITIES, ROOT, load as load_rules

RULES = load_rules()          # raises RulesError and stops the app if rules.txt is bad


def create_app(db_path: Optional[str] = None, rules=None) -> FastAPI:
    R = rules or RULES
    path = db_path or str(ROOT / R.s("backend", "db_path"))
    db = DB(path)
    app = FastAPI(title=R.s("meta", "app_name"))
    ttl = R.i("backend", "token_ttl_hours") * 3600
    now_ms = lambda: int(time.time() * 1000)

    # ---------- auth
    def hash_pw(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()

    def issue(uid):
        t = secrets.token_urlsafe(32)
        db.x("INSERT INTO tokens(token,user_id,expires) VALUES(?,?,?)", (t, uid, int(time.time()) + ttl))
        return t

    def user(authorization: str = Header(default="")):
        t = authorization.removeprefix("Bearer ").strip()
        r = db.one("SELECT user_id, expires FROM tokens WHERE token=?", (t,)) if t else None
        if not r or r["expires"] < time.time():
            raise HTTPException(401, "Sign in again.")
        return r["user_id"]

    class Cred(BaseModel):
        email: str = Field(pattern=r"^\S+@\S+\.\S+$")
        password: str

    @app.post("/api/register")
    def register(c: Cred):
        if len(c.password) < R.i("backend", "password_min_length"):
            raise HTTPException(400, f"Password needs at least {R.i('backend','password_min_length')} characters.")
        if db.one("SELECT 1 FROM users WHERE email=?", (c.email.lower(),)):
            raise HTTPException(409, "An account with this email already exists.")
        salt = secrets.token_hex(16)
        uid = db.x("INSERT INTO users(email,pw_hash,salt) VALUES(?,?,?)",
                   (c.email.lower(), hash_pw(c.password, salt), salt))
        return {"token": issue(uid)}

    @app.post("/api/login")
    def login(c: Cred):
        r = db.one("SELECT id, pw_hash, salt FROM users WHERE email=?", (c.email.lower(),))
        if not r or not secrets.compare_digest(r["pw_hash"], hash_pw(c.password, r["salt"])):
            raise HTTPException(401, "Email or password is wrong.")
        return {"token": issue(r["id"])}

    # ---------- helpers
    def load_p(uid):
        p, m = db.get_player(uid)
        if p is None:
            raise HTTPException(404, "No player yet.")
        return p, ml.Model(R, m)

    def save_p(uid, p, model):
        db.put_player(uid, p, model.to_json())

    def find_goal(p, gid):
        for g in p["goals"]:
            if g["id"] == gid:
                return g
        raise HTTPException(404, "Goal not found.")

    def find_ms(p, mid):
        for g in p["goals"]:
            for m in g["milestones"]:
                if m["id"] == mid:
                    return g, m
        raise HTTPException(404, "Milestone not found.")

    def find_q(p, qid):
        for g in p["goals"]:
            for m in g["milestones"]:
                for q in m["quests"]:
                    if q["id"] == qid:
                        return g, m, q
        raise HTTPException(404, "Quest not found.")

    def uid_():
        return int(time.time() * 1000) * 1000 + secrets.randbelow(1000)

    def learn(p, model, g, q, label, now):
        """Feed one outcome to the model. Advice only, never rewards."""
        x = ml.features(R, q, g["stat"], p, now)
        model.update(x, label)
        p["ml_rate"] = round(0.9 * p.get("ml_rate", 0.5) + 0.1 * label, 4)

    def sweep_stale(p, model, now):
        """Quests left open past the stale window count as a 0 outcome, once."""
        lim = R.i("suggest", "stale_quest_days") * 86400 * 1000
        changed = False
        for g in p["goals"]:
            for m in g["milestones"]:
                for q in m["quests"]:
                    if not q["done"] and not q.get("outcome_logged") and now - q["created"] > lim:
                        q["outcome_logged"] = True
                        learn(p, model, g, q, 0, now)
                        changed = True
        return changed

    def view(p, model, now):
        """State plus everything derived, so the frontend never recomputes rules."""
        lv = p["statLevel"]
        out = dict(p)
        out["log"] = p["log"][-30:]
        out["need"] = R.level_need(p["level"])
        out["tier"] = R.tier(p["level"])
        out["class"] = R.klass(p["level"])
        out["title"] = engine.profile_title(R, p)
        out["statReq"] = {k: R.stat_req(lv[k]) for k in lv}
        out["resting"] = {k: engine.is_resting(R, p, k, now) for k in lv}
        out["today"] = {"xp": engine.day_overall_xp(p, now),
                        "stat": {k: engine.day_stat_xp(p, k, now) for k in lv}}
        out["week"] = engine.week_key(now, R)
        advice = {}
        for g in p["goals"]:
            for m in g["milestones"]:
                for q in m["quests"]:
                    a = ml.advise(R, model, q, g["stat"], p, now)
                    h = engine.hint_for(R, q, now)
                    if h:
                        a["tips"].insert(0, h)
                    advice[str(q["id"])] = a
        out["advice"] = advice
        out["ml"] = {"version": model.version, "samples": model.samples,
                     "accuracy": round(model.acc_ema, 3), "history": model.history[-5:]}
        return out

    # ---------- public rules
    @app.get("/api/rules")
    def rules_ep():
        return R.public()

    # ---------- player
    class PlayerIn(BaseModel):
        name: str = Field(min_length=1, max_length=24)
        age: str = ""
        job: str = Field(default="", max_length=30)
        focus: str
        quest: str = Field(default="", max_length=60)
        rarity: str = "rare"

    @app.post("/api/player")
    def create_player(b: PlayerIn, uid: int = Depends(user)):
        if b.focus not in R.stat_keys or b.rarity not in RARITIES:
            raise HTTPException(400, "Unknown stat or rarity.")
        now = now_ms()
        p = engine.new_player(R, b.name.strip(), b.age, b.job.strip(), b.focus, now)
        q = {"id": uid_(), "text": b.quest.strip() or "Take your first step", "rarity": b.rarity,
             "done": False, "runs": [], "created": now, "est_minutes": 30, "resistance": 3}
        m = {"id": uid_(), "text": "First steps", "quests": [q]}
        p["goals"].append({"id": uid_(), "text": "My first goal", "stat": b.focus, "milestones": [m]})
        model = ml.Model(R)
        save_p(uid, p, model)
        return view(p, model, now)

    @app.get("/api/state")
    def state(uid: int = Depends(user)):
        p, model = load_p(uid)
        now = now_ms()
        if sweep_stale(p, model, now):
            save_p(uid, p, model)
        return view(p, model, now)

    @app.delete("/api/player")
    def reset(uid: int = Depends(user)):
        db.x("DELETE FROM players WHERE user_id=?", (uid,))
        return {"ok": True}

    # ---------- goals, milestones, quests
    class GoalIn(BaseModel):
        text: str = Field(min_length=1, max_length=60)
        stat: str

    class TextIn(BaseModel):
        text: str = Field(min_length=1, max_length=60)

    class QuestIn(BaseModel):
        text: str = Field(min_length=1, max_length=60)
        rarity: Optional[str] = None
        est_minutes: int = Field(default=30, ge=1, le=100000)
        resistance: int = Field(default=3, ge=1, le=5)
        multi_session: bool = False
        real_outcome: bool = False

    @app.post("/api/goals")
    def add_goal(b: GoalIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        if b.stat not in R.stat_keys:
            raise HTTPException(400, "Unknown stat.")
        p["goals"].append({"id": uid_(), "text": b.text.strip(), "stat": b.stat, "milestones": []})
        save_p(uid, p, model)
        return view(p, model, now_ms())

    @app.post("/api/goals/{gid}/milestones")
    def add_ms(gid: int, b: TextIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        find_goal(p, gid)["milestones"].append({"id": uid_(), "text": b.text.strip(), "quests": []})
        save_p(uid, p, model)
        return view(p, model, now_ms())

    @app.get("/api/suggest-rarity")
    def suggest(minutes: int = 30, resistance: int = 3, multi_session: bool = False,
                real_outcome: bool = False, uid: int = Depends(user)):
        return {"rarity": engine.suggest_rarity(R, minutes, resistance, multi_session, real_outcome)}

    @app.post("/api/milestones/{mid}/quests")
    def add_quest(mid: int, b: QuestIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        g, m = find_ms(p, mid)
        sug = engine.suggest_rarity(R, b.est_minutes, b.resistance, b.multi_session, b.real_outcome)
        rar = b.rarity or sug
        if rar not in RARITIES:
            raise HTTPException(400, "Unknown rarity.")
        m["quests"].append({"id": uid_(), "text": b.text.strip(), "rarity": rar, "done": False,
                            "runs": [], "created": now_ms(), "est_minutes": b.est_minutes,
                            "resistance": b.resistance, "suggested": sug})
        m.pop("bonus", None) if not all(x["done"] for x in m["quests"]) else None
        save_p(uid, p, model)
        return view(p, model, now_ms())

    class CompleteIn(BaseModel):
        effort: int = Field(ge=1, le=5)
        evidence: str = "none"
        note: str = Field(default="", max_length=200)
        proof: str = Field(default="", max_length=200)
        reflection: str = Field(default="", max_length=300)

    @app.post("/api/quests/{qid}/preview")
    def preview(qid: int, b: CompleteIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        g, m, q = find_q(p, qid)
        if b.evidence not in R.public()["evidence"]:
            raise HTTPException(400, "Unknown evidence level.")
        pv = engine.preview_stat_xp(R, p, g, q, b.effort, b.evidence, now_ms())
        return {"stat": g["stat"], **pv}

    @app.post("/api/quests/{qid}/complete")
    def complete(qid: int, b: CompleteIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        g, m, q = find_q(p, qid)
        now = now_ms()
        try:
            msgs, flash, info = engine.complete_quest(
                R, p, g, m, q, b.effort, b.evidence, b.note, b.proof, b.reflection,
                now_ms=now, sha=R.sha)
        except engine.RuleViolation as e:
            raise HTTPException(422, str(e))
        model.record_effort(q["rarity"], b.effort)
        learn(p, model, g, q, 1, now)
        save_p(uid, p, model)
        return {"messages": msgs, "flash": flash, "state": view(p, model, now)}

    @app.post("/api/quests/{qid}/skip")
    def skip(qid: int, uid: int = Depends(user)):
        p, model = load_p(uid)
        g, m, q = find_q(p, qid)
        if q["done"]:
            raise HTTPException(422, "Already logged.")
        if not q.get("outcome_logged"):
            q["outcome_logged"] = True
            learn(p, model, g, q, 0, now_ms())   # no stat change, no punishment
        save_p(uid, p, model)
        return view(p, model, now_ms())

    class ReviewIn(BaseModel):
        wins: str = Field(default="", max_length=300)
        block: str = Field(default="", max_length=300)
        next: str = Field(default="", max_length=300)
        energy: int = Field(default=0, ge=0, le=5)

    @app.post("/api/review")
    def review(b: ReviewIn, uid: int = Depends(user)):
        p, model = load_p(uid)
        try:
            msgs, flash = engine.save_review(R, p, b.wins, b.block, b.next, b.energy,
                                             now_ms=now_ms(), sha=R.sha)
        except engine.RuleViolation as e:
            raise HTTPException(422, str(e))
        save_p(uid, p, model)
        return {"messages": msgs, "flash": flash, "state": view(p, model, now_ms())}

    fe = ROOT / R.s("frontend", "static_dir")
    if fe.exists():
        app.mount("/", StaticFiles(directory=str(fe), html=True), name="ui")
    return app


app = create_app()
