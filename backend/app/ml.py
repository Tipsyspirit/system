"""The evolving model. Pure Python online logistic regression, one per player.

It learns from logged outcomes (completed = 1, skipped or stale = 0) and from the
effort ratings the player gives. It ADVISES only: it predicts how likely a quest is
to get done and suggests easier or harder rarity. It is never imported by engine.py,
so it cannot change XP, stats or levels ([invariants] ml_may_change_rewards = false).
"""
import copy
import math
from datetime import datetime

from .rules import Rules, RARITIES

RANK = {r: i for i, r in enumerate(RARITIES)}


def _sig(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def features(rules: Rules, quest: dict, goal_stat: str, player: dict, now_ms: int) -> list:
    """Feature vector in the order of [ml] features. Bias is added by the model."""
    d = datetime.fromtimestamp(now_ms / 1000)
    hour = d.hour + d.minute / 60
    wd = d.weekday()
    log = [e for e in player["log"] if e.get("type") == "quest"]
    last = log[-10:]
    rate = player.get("ml_rate", 0.5)
    # streak: consecutive days back from today with at least one quest
    days = {datetime.fromtimestamp(e["t"] / 1000).date() for e in log}
    streak, cur = 0, d.date()
    while cur in days:
        streak += 1
        cur = cur.fromordinal(cur.toordinal() - 1)
    lvl = player["statLevel"].get(goal_stat, 5)
    vals = {
        "rarity_rank": RANK[quest["rarity"]] / 3.0,
        "est_minutes": min(quest.get("est_minutes", 30), 600) / 600.0,
        "resistance": quest.get("resistance", 3) / 5.0,
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
        "weekday_sin": math.sin(2 * math.pi * wd / 7),
        "weekday_cos": math.cos(2 * math.pi * wd / 7),
        "streak_days": min(streak, 14) / 14.0,
        "recent_completion_rate": rate,
        "stat_level_norm": min(lvl, 30) / 30.0,
    }
    return [vals[k] for k in rules.l("ml", "features")]


class Model:
    def __init__(self, rules: Rules, state: dict = None):
        self.rules = rules
        n = len(rules.l("ml", "features")) + 1  # + bias
        s = state or {}
        self.w = s.get("w") or [0.0] * n
        if len(self.w) != n:  # feature list changed in rules.txt: start fresh, keep nothing stale
            self.w = [0.0] * n
        self.samples = s.get("samples", 0)
        self.acc_ema = s.get("acc_ema", 0.5)
        self.snapshots = s.get("snapshots", [])
        self.effort_ema = s.get("effort_ema", {})   # rarity -> EMA of effort ratings
        self.version = s.get("version", 0)
        self.history = s.get("history", [])         # short evolution log

    # ---- inference
    def predict(self, x):
        z = self.w[0] + sum(wi * xi for wi, xi in zip(self.w[1:], x))
        return _sig(z)

    # ---- evolution
    def update(self, x, label: int):
        r = self.rules
        if not r.b("ml", "enabled"):
            return
        p = self.predict(x)
        correct = (p >= 0.5) == (label == 1)
        self.acc_ema = 0.9 * self.acc_ema + 0.1 * (1.0 if correct else 0.0)
        self.samples += 1
        if self.samples < r.i("ml", "min_samples_to_adapt"):
            # warm-up: only record, no weight change
            return
        lr, l2, cap = r.f("ml", "learning_rate"), r.f("ml", "l2"), r.f("ml", "max_weight_step")
        err = label - p
        grads = [err] + [err * xi for xi in x]
        for i, g in enumerate(grads):
            step = lr * g - lr * l2 * self.w[i]
            step = max(-cap, min(cap, step))      # evolution guard
            self.w[i] += step
        self.version += 1
        if self.version % r.i("ml", "snapshot_every") == 0:
            self._snapshot()

    def _snapshot(self):
        r = self.rules
        snap = {"v": self.version, "w": list(self.w), "acc": round(self.acc_ema, 4)}
        # rollback if accuracy has dropped too far below the best snapshot
        if self.snapshots:
            best = max(self.snapshots, key=lambda s: s["acc"])
            if best["acc"] - self.acc_ema > r.f("ml", "rollback_if_accuracy_drop"):
                self.w = list(best["w"])
                self.acc_ema = best["acc"]
                self.history.append({"v": self.version, "event": "rollback", "to": best["v"]})
                self.history = self.history[-20:]
                return
        self.snapshots.append(snap)
        self.snapshots = self.snapshots[-r.i("ml", "keep_snapshots"):]
        self.history.append({"v": self.version, "event": "snapshot", "acc": snap["acc"]})
        self.history = self.history[-20:]

    def record_effort(self, rarity: str, effort: int):
        a = self.rules.f("ml", "effort_ema_alpha")
        prev = self.effort_ema.get(rarity, 3.0)
        self.effort_ema[rarity] = round((1 - a) * prev + a * effort, 3)

    def to_json(self):
        return {"w": self.w, "samples": self.samples, "acc_ema": self.acc_ema,
                "snapshots": self.snapshots, "effort_ema": self.effort_ema,
                "version": self.version, "history": self.history}


def advise(rules: Rules, model: Model, quest: dict, goal_stat: str, player: dict, now_ms: int) -> dict:
    """Advice only. Nothing here is ever applied automatically."""
    out = {"enabled": rules.b("ml", "enabled"), "p_complete": None, "tips": [],
           "suggested_rarity": None, "model_version": model.version, "samples": model.samples}
    if not out["enabled"]:
        return out
    x = features(rules, quest, goal_stat, player, now_ms)
    p = model.predict(x)
    learned = model.samples >= rules.i("ml", "min_samples_to_adapt")
    out["p_complete"] = round(p, 3) if learned else None
    if learned:
        if p < rules.f("ml", "low_completion_probability") and not quest["done"]:
            out["tips"].append("Unlikely to get done as written. Try a smaller step.")
        elif p > rules.f("ml", "high_completion_probability") and not quest["done"]:
            out["tips"].append("Very likely. Good one to do first.")
    avg = model.effort_ema.get(quest["rarity"])
    i = RANK[quest["rarity"]]
    if avg is not None and model.samples >= rules.i("ml", "min_samples_to_adapt"):
        if avg < rules.f("ml", "raise_rarity_if_avg_effort_below") and i < 3:
            out["suggested_rarity"] = RARITIES[i + 1]
            out["tips"].append(f"You rate {quest['rarity']} quests easy. Consider {RARITIES[i+1]}.")
        elif avg > rules.f("ml", "lower_rarity_if_avg_effort_above") and i > 0:
            out["suggested_rarity"] = RARITIES[i - 1]
            out["tips"].append(f"You rate {quest['rarity']} quests very hard. Consider {RARITIES[i-1]}.")
    return out
