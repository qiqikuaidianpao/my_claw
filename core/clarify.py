"""Clarify interaction — deterministic option matching and pending state.

The kernel exposes ``ask_user`` to the model; when called mid-task it writes
a pending clarification into the session KV storage and terminates the turn.
On the next turn the pre-flight phase matches the user's reply against the
pending options and, on a hit, folds the choice back into the original task.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

PENDING_KEY = "claw:pending_clarify"
MAX_MISSES = 2
EXPIRY_SECONDS = 24 * 3600

_NUM_CANON = {"一": 1, "1": 1, "①": 1, "二": 2, "2": 2, "②": 2, "三": 3, "3": 3, "③": 3, "四": 4, "4": 4, "④": 4}
_ORDINAL_RE = re.compile(r"第\s*([一二三四1-4])\s*[个条选项]?")
_PICK_RE = re.compile(r"(?:选|要|是)\s*([一二三四1-4])\s*号?")


@dataclass
class PendingClarify:
    original_query: str
    question: str
    options: list[str]
    asked_at: float = field(default_factory=time.time)
    misses: int = 0

    def expired(self) -> bool:
        return time.time() - self.asked_at > EXPIRY_SECONDS

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "PendingClarify | None":
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore"))
            return cls(
                original_query=str(data.get("original_query", "")),
                question=str(data.get("question", "")),
                options=[str(o) for o in data.get("options", [])],
                asked_at=float(data.get("asked_at", 0)),
                misses=int(data.get("misses", 0)),
            )
        except (ValueError, TypeError):
            return None


def match_option(reply: str, options: list[str]) -> int | None:
    """Return 0-based index if ``reply`` selects one of ``options`` else None.

    Matching order: bare ordinal → 第N个/条 → 选N/要N → option-text substring.
    """
    s = reply.strip().strip("。.!！")
    if not s or not options:
        return None

    # bare numeral with optional punctuation wrappers: "1" "2." "②" "3）"
    bare = s.strip(".()（）.、，, ")
    if bare in _NUM_CANON:
        idx = _NUM_CANON[bare] - 1
        if idx < len(options):
            return idx

    for pattern in (_ORDINAL_RE, _PICK_RE):
        m = pattern.search(s)
        if m:
            token = m.group(1)
            idx = _NUM_CANON.get(token, 0) - 1
            if 0 <= idx < len(options):
                return idx

    # option-text match: the reply IS a substring of an option or vice versa
    if len(s) >= 4:
        for i, opt in enumerate(options):
            opt_clean = opt.strip()
            if len(opt_clean) >= 4 and (s in opt_clean or opt_clean in s):
                return i
    return None


def format_question(question: str, options: list[str], hint_index: int | None = None) -> str:
    """Render the user-facing clarification prompt with emoji-numbered options.

    hint_index: option index the user picked on a similar past question —
    marked with a star so recurring choices are recognizable at a glance.
    """
    chips = []
    for i, (chip, opt) in enumerate(zip("1️⃣ 2️⃣ 3️⃣ 4️⃣".split(), options)):
        mark = " ⭐（上次的选择）" if hint_index == i else ""
        chips.append(f"{chip} {opt}{mark}")
    return f"🤔 {question}\n\n" + "\n".join(chips) + "\n\n回复数字即可，也可以直接补充说明。"


def build_continuation(pending: PendingClarify, chosen_index: int) -> str:
    """Fold the user's choice back into the original task for re-execution."""
    chosen = pending.options[chosen_index]
    return f"{pending.original_query}\n（用户澄清：选择了选项{chosen_index + 1} — {chosen}）"


# ── 澄清历史：同类问题记住用户的选择 ────────────────────────────────────────

HISTORY_LIMIT = 20
SIM_ANNOTATE = 0.45  # 与历史问题足够相似 → 在对应选项上标注“上次的选择”
SIM_AUTO = 0.60  # 高度相似且同一选项已被选过 ≥2 次 → 自动按历史选择继续


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def dice(a: str, b: str) -> float:
    """字符二元组 Dice 相似度（0~1），无依赖、确定性。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


class ClarifyHistory:
    """澄清选择的轻量历史：问题+选项+所选，用于选项标注与高频自动继续。"""

    def __init__(self, kv, app_id: str, user_id: str = "") -> None:
        self.kv = kv
        scope = f":user:{user_id}" if user_id else ""
        self._key = f"claw:clarify_history:{app_id or 'app'}{scope}"

    def _load(self) -> list[dict]:
        raw = self.kv.get(self._key)
        if not raw:
            return []
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def record(self, question: str, options: list[str], pick: int) -> None:
        if not (0 <= pick < len(options)):
            return
        items = self._load()
        items.append({"q": question[:200], "opts": [str(o)[:120] for o in options], "pick": pick})
        self.kv.set(self._key, json.dumps(items[-HISTORY_LIMIT:], ensure_ascii=False).encode("utf-8"))

    def lookup(self, question: str, options: list[str]) -> dict:
        """返回 {"annotate": 索引|None, "auto": 索引|None}。

        annotate：与最相似历史问题对应的当时所选（相似度≥SIM_ANNOTATE）；
        auto：高度相似（≥SIM_AUTO）的记录中同一选项被选过≥2次——直接照做不打断。
        """
        best_sim, best_pick = 0.0, None
        auto_counts: dict[int, int] = {}
        for rec in self._load():
            sim = dice(question, str(rec.get("q", "")))
            if sim >= SIM_AUTO:
                auto_counts[rec["pick"]] = auto_counts.get(rec["pick"], 0) + 1
            if sim > best_sim and 0 <= rec.get("pick", -1) < len(options):
                best_sim, best_pick = sim, rec["pick"]
        auto = None
        for idx, n in auto_counts.items():
            if n >= 2 and idx < len(options):
                auto = idx
        return {"annotate": best_pick if best_sim >= SIM_ANNOTATE else None, "auto": auto}
