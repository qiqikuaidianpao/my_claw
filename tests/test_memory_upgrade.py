"""E4 memory management commands — routing guards and store round-trips."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory.commands import execute_memory_command, format_memory_list, parse_memory_command
from core.memory.persona import PersonaStore


class FakeKV:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: bytes):
        self.data[key] = value


def seeded_persona() -> PersonaStore:
    p = PersonaStore(FakeKV(), app_id="a1", user_id="u1")
    p.write("MEMORY.md", "称呼：boss，偏好简洁汇报。\n")
    p.merge_managed({"user_facts": ["本金500万，利率6%", "项目期限36期"], "preferences": ["汇报要带结论"]})
    return p


class TestRouting(unittest.TestCase):
    def test_list_variants(self):
        for q in ("查看记忆", "帮我看看记忆", "列出记忆", "显示一下记忆", "记忆列表", "记忆清单", "请查看记忆"):
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "list", q)

    def test_delete_variants(self):
        cases = {
            "删除记忆2": 1,
            "忘记记忆1": 0,
            "删除第3条记忆": 2,
            "删掉记忆十二": 11,
            "清除记忆 2": 1,
        }
        for q, idx in cases.items():
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "delete", q)
            self.assertEqual(cmd.index, idx, q)

    def test_edit_variants(self):
        cases = {
            "修改记忆1为喜欢简洁汇报": (0, "喜欢简洁汇报"),
            "修改第2条记忆为期限24期": (1, "期限24期"),
            "把记忆1改成每天9点提醒": (0, "每天9点提醒"),
            "记忆1改为偏好表格汇报": (0, "偏好表格汇报"),
            "更新记忆3为新的偏好": (2, "新的偏好"),
        }
        for q, (idx, new) in cases.items():
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "edit", q)
            self.assertEqual(cmd.index, idx, q)
            self.assertEqual(cmd.new_text, new, q)

    def test_questions_do_not_route(self):
        for q in (
            "你记得我生日吗",
            "记住这个词",
            "怎么删除记忆",
            "能不能查看记忆",
            "你能修改记忆吗",
            "帮我删除记忆里关于项目的部分",
            "我们聊聊记忆管理吧",
            "帮我整理一下租赁的资料",
            "现在几点了",
            "",
        ):
            self.assertIsNone(parse_memory_command(q), q)

    def test_long_text_not_routed(self):
        self.assertIsNone(parse_memory_command("查看记忆" + "，" + "顺便" * 40))


class TestRoundTrip(unittest.TestCase):
    def test_list_shows_numbered_entries_and_free_text(self):
        p = seeded_persona()
        text = format_memory_list(p)
        self.assertIn("称呼：boss", text)
        self.assertIn("1. 本金500万，利率6%", text)
        self.assertIn("2. 项目期限36期", text)
        self.assertIn("3. 汇报要带结论", text)
        self.assertIn("共 3 条", text)

    def test_delete_removes_exact_entry(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("删除记忆1"))
        self.assertIn("已删除第 1 条", reply)
        self.assertIn("本金500万", reply)
        remaining = [item for _, item in p.managed_entries()]
        self.assertNotIn("本金500万，利率6%", remaining)
        self.assertEqual(len(remaining), 2)
        # free text untouched
        self.assertIn("称呼：boss", p.free_text())

    def test_delete_out_of_range(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("删除记忆9"))
        self.assertIn("没有第 9 条", reply)
        self.assertEqual(len(p.managed_entries()), 3)

    def test_edit_updates_in_place(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("修改记忆2为项目期限24期"))
        self.assertIn("已更新第 2 条", reply)
        self.assertIn("旧：项目期限36期", reply)
        self.assertIn("新：项目期限24期", reply)
        remaining = [item for _, item in p.managed_entries()]
        self.assertIn("项目期限24期", remaining)
        self.assertNotIn("项目期限36期", remaining)
        self.assertEqual(len(remaining), 3)

    def test_edit_out_of_range(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("修改记忆5为X"))
        self.assertIn("没有第 5 条", reply)
        self.assertEqual(len(p.managed_entries()), 3)

    def test_empty_memory_list(self):
        p = PersonaStore(FakeKV(), app_id="a1")
        reply = execute_memory_command(p, parse_memory_command("查看记忆"))
        self.assertIn("还没有沉淀的记忆条目", reply)

    def test_delete_on_empty_store(self):
        p = PersonaStore(FakeKV(), app_id="a1")
        reply = execute_memory_command(p, parse_memory_command("删除记忆1"))
        self.assertIn("没有任何记忆条目", reply)

    def test_merge_after_delete_keeps_others(self):
        p = seeded_persona()
        execute_memory_command(p, parse_memory_command("删除记忆3"))
        p.merge_managed({"preferences": ["汇报要带结论"]})  # re-merge dedupes, no zombie
        items = [item for sec, item in p.managed_entries() if sec == "preferences"]
        self.assertEqual(items, ["汇报要带结论"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
