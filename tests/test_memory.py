"""Memory, persona and context manager tests (fake KV storage)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import ContextManager, HistoryStore, estimate_messages_tokens
from core.memory.persona import PersonaStore
from core.memory.service import MemoryService
from core.session import SessionContext


class FakeKV:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: bytes):
        self.data[key] = value

    def delete(self, key: str):
        self.data.pop(key, None)


class TestPersonaStore(unittest.TestCase):
    def test_roundtrip_and_user_scope(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1", user_id="u1")
        p.write("IDENTITY.md", "# 身份\nmy_claw")
        p.write("USER.md", "称呼：老板")
        self.assertIn("my_claw", p.read("IDENTITY.md"))
        self.assertIn("老板", p.read("USER.md"))

    def test_reset(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        for name in ("IDENTITY.md", "USER.md", "SOUL.md", "MEMORY.md"):
            p.write(name, "x")
        p.reset(keep_identity=True)
        self.assertEqual(p.read("USER.md"), "")
        self.assertEqual(p.read("IDENTITY.md"), "x")

    def test_managed_merge_dedup(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        p.merge_managed({"project_facts": ["本金500万", "利率6%"]})
        p.merge_managed({"project_facts": ["利率6%", "期限36期"]})
        managed = p.read_managed()
        self.assertEqual(len(managed["project_facts"]), 3)
        memory = p.read("MEMORY.md")
        self.assertIn("## Managed Memory (auto)", memory)

    def test_build_context(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        p.write("IDENTITY.md", "身份内容")
        p.write("MEMORY.md", "记忆内容")
        ctx = p.build_context()
        self.assertIn("身份内容", ctx)
        self.assertIn("长期记忆", ctx)


class TestMemoryService(unittest.TestCase):
    def test_digest_and_recall(self):
        kv = FakeKV()
        m = MemoryService(kv, app_id="a1")
        m.append_digest("算一下租赁", "月供152,109.69元")
        recent = m.recent_context()
        self.assertIn("月供152,109.69元", recent)

    def test_digest_cap(self):
        kv = FakeKV()
        m = MemoryService(kv, app_id="a1")
        m.append_digest("x" * 100, "y" * 100)
        raw = kv.get(list(kv.data.keys())[0])
        self.assertLessEqual(len(raw), 21000)

    def test_gc_marker(self):
        kv = FakeKV()
        m = MemoryService(kv, app_id="a1")
        m.append_digest("a", "b")
        m.gc(force=True)
        # second gc same day is a no-op
        self.assertEqual(m.gc(), 0)


class TestContextManager(unittest.TestCase):
    def test_no_compact_under_budget(self):
        ctx = SessionContext(session_id="s", messages=[{"role": "user", "content": "hi"}])
        ContextManager(budget_tokens=100000).compact_if_needed(ctx)
        self.assertEqual(len(ctx.messages), 1)

    def test_compact_keeps_recent_tail(self):
        msgs = [{"role": "user", "content": f"消息{i}：" + "内容" * 200} for i in range(20)]
        msgs.append({"role": "user", "content": "最后一条"})
        ctx = SessionContext(session_id="s", messages=msgs)
        cm = ContextManager(llm=None, budget_tokens=100, keep_recent_tokens=50)
        cm.compact_if_needed(ctx)
        self.assertLess(len(ctx.messages), len(msgs))
        self.assertIn("最后一条", ctx.messages[-1]["content"])
        self.assertTrue(any("摘要" in str(m.get("content")) or "概要" in str(m.get("content")) for m in ctx.messages))

    def test_token_estimate(self):
        self.assertEqual(estimate_messages_tokens([{"role": "user", "content": "x" * 400}]), 100)


class TestHistoryStore(unittest.TestCase):
    def test_append_load(self):
        kv = FakeKV()
        h = HistoryStore(kv, conversation_id="c1")
        h.append("问题1", "回答1")
        h.append("问题2", "回答2")
        turns = h.load()
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["u"], "问题1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
