"""Pure game logic. No I/O. Every number comes from Rules."""
import math
import time
from datetime import datetime, timedelta

from .rules import Rules, RARITIES, EVIDENCE


class RuleViolation(Exception):
    pass


def day_start_ms(now_ms: int) -> int:
    d = datetime.fromtimestamp(now_ms / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


def week_key(now_ms: int, rules: Rules) -> str:
    d = datetime.fromtimestamp(now_ms / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    d -= timedelta(days=d.weekday())  # monday
    return d.strftime("%Y-%m-%d")


def new_player(rules: Rules, name, age, job, focus_stat, now_ms=None) -> dict:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    start = rules.i("stats", "start_level")
    keys = rules.stat_keys
    return {
        "name": name, "age": age or "", "job": job or "Unassigned",
        "level": 1, "xp": 0, "mp": rules.i("level", "mp_max"),
        "created": now, "focus": focus_stat,
        "statLevel": {k: start for k in keys},
        "statXp": {k: 0 for k in keys},
        "statLast": {k: now for k in keys},
        "goals": [], "reviews": {}, "log": [],
    }


def stat_req(rules, lvl):
    return rules.stat_req(lvl)


def add_stat_xp(rules, p, stat, amount):
    """Add stat XP, level up while over threshold. Returns new level if it rose else 0."""
    p["statXp"][stat] = p["statXp"].get(stat, 0) + amount
    rose = 0
    while p["statXp"][stat] >= rules.stat_req(p["statLevel"][stat]):
        p["statXp"][stat] -= rules.stat_req(p["statLevel"][stat])
        p["statLevel"][stat] += 1
        rose = p["statLevel"][stat]
    p["statXp"][stat] = round(p["statXp"][stat], 1)
    return rose


def add_overall_xp(rules, p, amount):
    p["xp"] += amount
    up = False
    while p["xp"] >= rules.level_need(p["level"]):
        p["xp"] -= rules.level_need(p["level"])
        p["level"] += 1
        up = True
    if up and rules.b("level", "level_up_restores_mp"):
        p["mp"] = rules.i("level", "mp_max")
    return up


def day_overall_xp(p, now_ms):
    t0 = day_start_ms(now_ms)
    return sum(e.get("xp", 0) for e in p["log"] if e["t"] >= t0 and e.get("type") == "quest")


def day_stat_xp(p, stat, now_ms):
    t0 = day_start_ms(now_ms)
    return sum(e.get("sx", 0) for e in p["log"]
               if e["t"] >= t0 and e.get("type") == "quest" and e.get("stat") == stat)


def is_resting(rules, p, stat, now_ms):
    days = rules.i("rest", "resting_after_days")
    return now_ms - p["statLast"].get(stat, p["created"]) > days * 86400 * 1000


def runs_today(q, now_ms):
    t0 = day_start_ms(now_ms)
    return sum(1 for r in q.get("runs", []) if r["t"] >= t0)


def profile_title(rules, p) -> str:
    L = p["statLevel"]
    mx = max(L.values())
    top = [k for k, v in L.items() if v == mx]
    if len(top) == 1 and mx >= rules.i("titles", "single_top_min_level"):
        return rules.s("titles", top[0])
    within = rules.i("titles", "balanced_within_levels")
    near = [k for k, v in L.items() if v >= mx - within]
    if mx >= rules.i("titles", "balanced_min_top_level") and len(near) >= 2:
        return rules.s("titles", "balanced_title")
    return ""


def suggest_rarity(rules, minutes, resistance, multi_session=False, real_outcome=False):
    """Rule-based rarity from estimated time and resistance."""
    if multi_session and real_outcome and minutes > 600:
        return "legendary"
    if (multi_session or real_outcome) and resistance >= rules.i("rarity.rules", "epic_min_resistance"):
        return "epic"
    if minutes <= rules.i("rarity.rules", "common_max_minutes") and resistance <= 2:
        return "common"
    if minutes <= rules.i("rarity.rules", "rare_max_minutes") or resistance <= 3:
        return "rare"
    return "epic"


def check_evidence(rules, p, q, evidence, note, proof, reflection, now_ms):
    if evidence not in EVIDENCE:
        raise RuleViolation("Unknown evidence level.")
    if evidence == "note" and not note.strip():
        raise RuleViolation("Add a short note about what you did.")
    if evidence in ("proof", "proof_reflection") and not proof.strip():
        raise RuleViolation("Add proof: a link, a file name or what you can show.")
    if evidence == "proof_reflection" and not reflection.strip():
        raise RuleViolation("Add a short reflection.")
    rar = q["rarity"]
    if rar == "epic" and evidence not in rules.l("rarity.rules", "epic_requires_evidence"):
        raise RuleViolation("Epic quests need proof or proof with reflection.")
    if rar == "legendary":
        if evidence not in rules.l("rarity.rules", "legendary_requires_evidence"):
            raise RuleViolation("Legendary quests need proof with reflection.")
        if rules.b("rarity.rules", "legendary_requires_review") and \
                week_key(now_ms, rules) not in p["reviews"]:
            raise RuleViolation("Legendary quests need this week's review saved first.")


def preview_stat_xp(rules, p, goal, q, effort, evidence, now_ms):
    rep = runs_today(q, now_ms) + 1
    rm = rules.repeat_mult(rep)
    rest = is_resting(rules, p, goal["stat"], now_ms)
    comeback = rules.f("rest", "comeback_multiplier") if rest else 1.0
    raw = (rules.base_stat_xp(q["rarity"]) * rules.effort_mult(effort)
           * rules.evidence_mult(evidence) * rm * comeback)
    return {"raw": raw, "repeat": rep, "repeat_mult": rm, "resting": rest}


def complete_quest(rules, p, goal, milestone, q, effort, evidence,
                   note="", proof="", reflection="", now_ms=None, sha=""):
    """Apply one completion. Returns (messages, leveled_flags). Mutates p."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if effort not in range(1, 6):
        raise RuleViolation("Effort must be 1 to 5.")
    repeatable = q["rarity"] == "common"
    if q["done"] and not repeatable:
        raise RuleViolation("Already logged.")
    check_evidence(rules, p, q, evidence, note, proof, reflection, now)

    stat = goal["stat"]
    pv = preview_stat_xp(rules, p, goal, q, effort, evidence, now)
    flags, msgs = [], []
    sx = pv["raw"]
    room = max(0, rules.i("caps", "stat_xp_per_day_per_stat") - day_stat_xp(p, stat, now))
    if sx > room:
        sx = room
        flags.append(f"Daily {stat} cap")
    sx = round(sx, 1)
    xp = rules.overall_xp(q["rarity"])
    xroom = max(0, rules.i("caps", "overall_xp_per_day") - day_overall_xp(p, now))
    if xp > xroom:
        xp = xroom
        flags.append("Daily XP cap")
    if pv["resting"]:
        flags.append("Comeback +20%")
    if pv["repeat"] > 1:
        flags.append(f"Repeat {pv['repeat']} today x{pv['repeat_mult']}")

    c0 = rules.klass(p["level"])
    q["done"] = True
    q["doneAt"] = now
    q.setdefault("runs", []).append({"t": now, "effort": effort, "ev": evidence})
    p["mp"] = max(0, p["mp"] - rules.i("level", "mp_cost_per_quest"))
    up = add_overall_xp(rules, p, xp)
    lv = add_stat_xp(rules, p, stat, sx)
    p["statLast"][stat] = now
    detail = " | ".join(x.strip() for x in (note, proof, reflection) if x.strip())
    _log(p, rules, now, sha, type="quest", text=q["text"], rarity=q["rarity"], stat=stat,
         xp=xp, sx=sx, effort=effort, ev=evidence, detail=detail, flags=flags)
    msgs.append(f"Quest complete. +{xp} XP")
    msgs.append(f"{stat} +{sx:g} XP" if sx > 0 else f"{stat} daily cap reached")
    if pv["resting"]:
        msgs.append("Comeback bonus +20%")
    if lv:
        msgs.append(f"{stat} increased to {lv}")

    if all(x["done"] for x in milestone["quests"]) and not milestone.get("bonus"):
        milestone["bonus"] = True
        bx = rules.i("bonus", "milestone_overall_xp")
        bs = rules.i("bonus", "milestone_stat_xp")
        up = add_overall_xp(rules, p, bx) or up
        l2 = add_stat_xp(rules, p, stat, bs)
        _log(p, rules, now, sha, type="milestone", text=f"Milestone cleared: {milestone['text']}",
             stat=stat, xp=bx, sx=bs, flags=["Bonus, exempt from caps"])
        msgs.append(f"Milestone cleared. +{bx} XP, {stat} +{bs} XP")
        if l2:
            msgs.append(f"{stat} increased to {l2}")

    if not goal.get("bonus") and goal["milestones"] and all(
            m["quests"] and all(x["done"] for x in m["quests"]) for m in goal["milestones"]):
        goal["bonus"] = True
        gs = rules.i("bonus", "goal_stat_xp")
        l3 = add_stat_xp(rules, p, stat, gs)
        _log(p, rules, now, sha, type="goal", text=f"Goal complete: {goal['text']}",
             stat=stat, xp=0, sx=gs, flags=["Bonus, exempt from caps"])
        msgs.append(f"Goal complete. {stat} +{gs} XP")
        if l3:
            msgs.append(f"{stat} increased to {l3}")

    flash = False
    if up:
        msgs.append(f"LEVEL UP. Level {p['level']}, MP restored")
        flash = True
    if rules.klass(p["level"]) != c0:
        msgs.append(f"CLASS UP. {rules.klass(p['level'])} class awakened")
        flash = True
    return msgs, flash, {"stat": stat, "sx": sx, "xp": xp}


def save_review(rules, p, wins, block, nxt, energy, now_ms=None, sha=""):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    wk = week_key(now, rules)
    first = wk not in p["reviews"]
    if first and rules.b("review", "requires_content") and not (wins.strip() or block.strip() or nxt.strip()):
        raise RuleViolation("Write at least one answer first.")
    p["reviews"][wk] = {"wins": wins.strip(), "block": block.strip(), "next": nxt.strip(),
                        "energy": energy, "at": now}
    if not first:
        return ["Review updated."], False
    c0 = rules.klass(p["level"])
    xp, sx, stat = rules.i("review", "overall_xp"), rules.i("review", "stat_xp"), rules.s("review", "stat")
    up = add_overall_xp(rules, p, xp)
    lv = add_stat_xp(rules, p, stat, sx)
    if rules.b("review", "restore_mp"):
        p["mp"] = rules.i("level", "mp_max")
    p["statLast"][stat] = now
    _log(p, rules, now, sha, type="review", text="Weekly review", stat=stat, xp=xp, sx=sx,
         flags=["Exempt from caps"])
    msgs = [f"Review saved. +{xp} XP", f"{stat} +{sx} XP", "MP restored"]
    if lv:
        msgs.append(f"{stat} increased to {lv}")
    flash = False
    if up:
        msgs.append(f"LEVEL UP. Level {p['level']}")
        flash = True
    if rules.klass(p["level"]) != c0:
        msgs.append(f"CLASS UP. {rules.klass(p['level'])} class awakened")
        flash = True
    return msgs, flash


def _log(p, rules, now, sha, **e):
    e["t"] = now
    if sha and rules.b("backend", "log_rules_hash_with_each_event"):
        e["rules"] = sha[:12]
    p["log"].append(e)
    if len(p["log"]) > 400:
        p["log"] = p["log"][-400:]


def hint_for(rules, q, now_ms):
    runs = q.get("runs", [])[-3:]
    n = rules.i("suggest", "easy_streak_to_raise")
    lim = rules.i("suggest", "easy_rating_max")
    if len(runs) >= n and all(r.get("effort", 9) <= lim for r in runs):
        return "Rated easy 3 times. Try a harder quest."
    if not q["done"] and now_ms - q.get("created", now_ms) > rules.i("suggest", "stale_quest_days") * 86400 * 1000:
        return "Open for 14+ days. Try a smaller step."
    return ""
