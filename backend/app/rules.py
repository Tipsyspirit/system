"""Load and validate rules.txt. The rest of the app reads numbers only from here."""
import configparser
import hashlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "rules.txt"

REQUIRED_FALSE = [
    "manual_stat_points", "buy_stats", "random_stat_rewards",
    "stat_loss_on_inactivity", "level_loss", "reward_app_opens",
    "ml_may_change_rewards", "history_rewritable",
]
RARITIES = ["common", "rare", "epic", "legendary"]
EVIDENCE = ["none", "note", "proof", "proof_reflection"]


class RulesError(Exception):
    pass


def _bool(v: str) -> bool:
    return v.strip().lower() in ("true", "yes", "1", "on")


def _list(v: str):
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Rules:
    raw: configparser.ConfigParser
    version: str
    sha: str

    # ---- generic access
    def s(self, section, key):
        try:
            return self.raw.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            raise RulesError(f"rules.txt missing [{section}] {key}")

    def f(self, section, key):
        return float(self.s(section, key))

    def i(self, section, key):
        return int(float(self.s(section, key)))

    def b(self, section, key):
        return _bool(self.s(section, key))

    def l(self, section, key):
        return _list(self.s(section, key))

    def section(self, name):
        if not self.raw.has_section(name):
            raise RulesError(f"rules.txt missing [{name}]")
        return dict(self.raw.items(name))

    # ---- typed helpers
    @property
    def stat_keys(self):
        return self.l("stats", "keys")

    @property
    def stat_labels(self):
        return self.section("stats.labels")

    def stat_req(self, level: int) -> int:
        return self.i("stats", "req_base") + self.i("stats", "req_per_lvl") * level

    def level_need(self, level: int) -> int:
        return self.i("level", "need_base") + (level - 1) * self.i("level", "need_per_lvl")

    def base_stat_xp(self, rarity):
        return self.f("rarity.stat_xp", rarity)

    def overall_xp(self, rarity):
        return self.i("rarity.overall_xp", rarity)

    def effort_mult(self, effort: int):
        return self.f("effort.multiplier", str(effort))

    def evidence_mult(self, ev: str):
        return self.f("evidence.multiplier", ev)

    def repeat_mult(self, n: int):
        if n <= 1:
            return self.f("repeat.multiplier", "first")
        if n == 2:
            return self.f("repeat.multiplier", "second")
        return self.f("repeat.multiplier", "third_plus")

    def tier(self, level: int) -> str:
        best, best_min = None, -1
        for name, mn in self.section("level.tiers").items():
            if int(mn) <= level and int(mn) > best_min:
                best, best_min = name, int(mn)
        return best or ""

    def klass(self, level: int) -> str:
        best, best_min = "", -1
        for name, mn in self.section("level.classes").items():
            if int(mn) <= level and int(mn) > best_min:
                best, best_min = name, int(mn)
        return best

    def public(self) -> dict:
        """Everything the frontend may need, straight from rules.txt."""
        return {
            "version": self.version,
            "sha": self.sha[:12],
            "stat_keys": self.stat_keys,
            "stat_labels": self.stat_labels,
            "stat_start": self.i("stats", "start_level"),
            "rarity": {
                r: {"xp": self.overall_xp(r), "stat_xp": self.base_stat_xp(r)}
                for r in RARITIES
            },
            "effort": {str(k): self.effort_mult(k) for k in range(1, 6)},
            "evidence": {e: self.evidence_mult(e) for e in EVIDENCE},
            "caps": {
                "xp": self.i("caps", "overall_xp_per_day"),
                "stat": self.i("caps", "stat_xp_per_day_per_stat"),
            },
            "review": {"xp": self.i("review", "overall_xp"),
                       "stat_xp": self.i("review", "stat_xp"),
                       "stat": self.s("review", "stat")},
            "rest_days": self.i("rest", "resting_after_days"),
            "mp_max": self.i("level", "mp_max"),
        }


def validate(r: Rules):
    for k in REQUIRED_FALSE:
        if r.b("invariants", k):
            raise RulesError(f"Invariant broken: [invariants] {k} must be false")
    keys = r.stat_keys
    if not keys:
        raise RulesError("[stats] keys is empty")
    for k in keys:
        if k not in r.stat_labels:
            raise RulesError(f"[stats.labels] missing {k}")
    for rar in RARITIES:
        r.base_stat_xp(rar); r.overall_xp(rar)
    for e in range(1, 6):
        r.effort_mult(e)
    for e in EVIDENCE:
        r.evidence_mult(e)
    if r.i("level", "stat_points_on_level_up") != 0:
        raise RulesError("stat_points_on_level_up must be 0")
    if r.s("review", "stat") not in keys:
        raise RulesError("[review] stat must be one of [stats] keys")
    if r.i("caps", "stat_xp_per_day_per_stat") <= 0 or r.i("caps", "overall_xp_per_day") <= 0:
        raise RulesError("caps must be positive")
    if not r.raw.has_section("level.tiers") or not r.section("level.tiers"):
        raise RulesError("[level.tiers] is empty")
    # derived monotonicity: harder rarity must never pay less stat XP
    xs = [r.base_stat_xp(x) for x in RARITIES]
    if xs != sorted(xs):
        raise RulesError("[rarity.stat_xp] must increase with rarity")
    if r.f("ml", "max_weight_step") <= 0:
        raise RulesError("[ml] max_weight_step must be > 0")


def load(path: Path = RULES_PATH) -> Rules:
    if not Path(path).exists():
        raise RulesError(f"rules file not found: {path}")
    text = Path(path).read_text(encoding="utf-8")
    cp = configparser.ConfigParser(inline_comment_prefixes=("#",), interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(text)
    except configparser.Error as e:
        raise RulesError(f"rules.txt is malformed: {e}")
    r = Rules(raw=cp, version=cp.get("meta", "rules_version", fallback="0"),
              sha=hashlib.sha256(text.encode()).hexdigest())
    validate(r)
    return r
