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


def format_question(question: str, options: list[str]) -> str:
    """Render the user-facing clarification prompt with emoji-numbered options."""
    chips = [f"{chip} {opt}" for chip, opt in zip("1️⃣ 2️⃣ 3️⃣ 4️⃣".split(), options)]
    return f"🤔 {question}\n\n" + "\n".join(chips) + "\n\n回复数字即可，也可以直接补充说明。"


def build_continuation(pending: PendingClarify, chosen_index: int) -> str:
    """Fold the user's choice back into the original task for re-execution."""
    chosen = pending.options[chosen_index]
    return f"{pending.original_query}\n（用户澄清：选择了选项{chosen_index + 1} — {chosen}）"
