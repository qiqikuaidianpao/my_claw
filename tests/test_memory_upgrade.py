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


# ═══ E5: daily extraction + consolidation ("dream") ═══════════════════════

from core.memory import consolidate as cons


class FakeLLM:
    """invoke_text stub returning a canned payload."""

    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[str] = []

    def invoke_text(self, system: str, messages: list) -> str:
        self.calls.append(system)
        return self.payload


def seeded_service(yesterday_digest: str = "") -> tuple:
    from core.memory.service import MemoryService

    kv = FakeKV()
    m = MemoryService(kv, app_id="a1", user_id="u1")
    p = PersonaStore(kv, app_id="a1", user_id="u1")
    if yesterday_digest:
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        kv.data[m._day_key(day)] = yesterday_digest.encode("utf-8")
    return kv, m, p


class TestDailyExtract(unittest.TestCase):
    def test_extracts_three_types_with_prefixes_and_date(self):
        kv, m, p = seeded_service("# 昨日\n- 用户: 项目A本金5000万\n- 助手: 已按偏好输出简报")
        llm = FakeLLM(
            '{"user_preferences": ["[偏好] 汇报先给结论"], "project_facts": ["[事实] 本金5000万"], '
            '"episodic": ["[经历] 处理了项目A测算（来自8月24日对话）"]}'
        )
        self.assertTrue(cons.daily_extract(m, p, llm))
        managed = dict((sec, items) for sec, items in p.read_managed().items())
        flat = {item for _, item in p.managed_entries()}
        self.assertIn("[偏好] 汇报先给结论", flat)
        self.assertIn("[事实] 本金5000万", flat)
        self.assertTrue(any(i.startswith("[经历] 处理了项目A测算") and "（来自8月24日对话）" in i for i in flat))

    def test_runs_once_per_day(self):
        kv, m, p = seeded_service("# 昨日\n- 用户: x\n- 助手: y")
        llm = FakeLLM("{}")
        self.assertTrue(cons.daily_extract(m, p, llm))  # marker set even for {}
        self.assertFalse(cons.daily_extract(m, p, llm))  # second call no-op

    def test_no_digest_skips_llm(self):
        kv, m, p = seeded_service("")
        llm = FakeLLM('{"project_facts": ["[事实] 不应出现"]}')
        self.assertFalse(cons.daily_extract(m, p, llm))
        self.assertEqual(llm.calls, [])
        self.assertEqual(p.managed_entries(), [])

    def test_parse_failure_no_merge(self):
        kv, m, p = seeded_service("# 昨日\n- 用户: x\n- 助手: y")
        llm = FakeLLM("这不是JSON")
        self.assertFalse(cons.daily_extract(m, p, llm))
        self.assertEqual(p.managed_entries(), [])


class TestConsolidate(unittest.TestCase):
    def _seeded(self, n: int, stale: int = 0):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1", user_id="u1")
        d = datetime.now() - timedelta(days=90)
        items = [f"[事实] 条目{i}，内容各不相同" for i in range(n - stale)]
        items += [f"[经历] 久远经历{j}（来自{d.month}月{d.day}日对话）" for j in range(stale)]
        sections = ("user_facts", "project_facts", "user_preferences", "episodic")
        for i, item in enumerate(items):  # spread across sections (merge caps 12/section)
            p.merge_managed({sections[i % len(sections)]: [item]})
        return p

    def test_under_threshold_untouched(self):
        p = self._seeded(10)
        llm = FakeLLM('["whatever"]')
        self.assertEqual(cons.consolidate(p, llm), "")
        self.assertEqual(len(p.managed_entries()), 10)

    def test_merge_and_archive_and_bak(self):
        p = self._seeded(40, stale=5)
        keep = [item for _, item in p.managed_entries()]
        llm = FakeLLM(json.dumps(keep, ensure_ascii=False))
        note = cons.consolidate(p, llm)
        self.assertIn("记忆整理完成", note)
        # stale episodic archived out of MEMORY.md
        self.assertFalse(any("久远经历" in item for _, item in p.managed_entries()))
        archive = p.read("MEMORY.archive.md")
        self.assertIn("久远经历0", archive)
        self.assertIn("## 归档于", archive)
        # bak holds the pre-consolidation original
        self.assertIn("久远经历0", p.read("MEMORY.bak.md"))
        self.assertIn("条目0", p.read("MEMORY.bak.md"))

    def test_parse_failure_leaves_memory_unchanged(self):
        p = self._seeded(40)
        before = p.read("MEMORY.md")
        llm = FakeLLM("垃圾输出")
        note = cons.consolidate(p, llm)
        self.assertIn("解析失败", note)
        self.assertEqual(p.read("MEMORY.md"), before)
        self.assertEqual(p.read("MEMORY.bak.md"), "")  # bak not written

    def test_second_consolidate_overwrites_previous_bak(self):
        p = self._seeded(40)
        keep = [item for _, item in p.managed_entries()]
        cons.consolidate(p, FakeLLM(json.dumps(keep, ensure_ascii=False)))
        first_bak = p.read("MEMORY.bak.md")
        p.merge_managed({"user_facts": ["[事实] 新增一条让条目再次超限" + "填充" * 5]})
        keep2 = [item for _, item in p.managed_entries()]
        cons.consolidate(p, FakeLLM(json.dumps(keep2, ensure_ascii=False)))
        second_bak = p.read("MEMORY.bak.md")
        self.assertNotEqual(first_bak, second_bak)  # single generation, overwritten

    def test_stale_detection_helper(self):
        d = datetime.now() - timedelta(days=90)
        old = f"[经历] 老经历（来自{d.month}月{d.day}日对话）"
        fresh = "[经历] 新经历（来自今天对话）"
        self.assertTrue(cons._stale_episodic(old))
        self.assertFalse(cons._stale_episodic(fresh))
        self.assertFalse(cons._stale_episodic("[事实] 无日期不是经历"))


class TestArchiveView(unittest.TestCase):
    def test_view_archive_routing_and_empty(self):
        from core.memory.commands import execute_memory_command, parse_memory_command

        cmd = parse_memory_command("查看归档记忆")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.action, "archive")
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        self.assertIn("归档记忆是空的", execute_memory_command(p, cmd))

    def test_list_mentions_archive_count(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        p.merge_managed({"user_facts": ["[事实] x"]})
        p.write("MEMORY.archive.md", "# 归档记忆\n## 归档于 2026-08-24\n- [经历] 老经历\n")
        text = format_memory_list(p)
        self.assertIn("另有 1 条已归档", text)


import json
import os
from datetime import datetime, timedelta


class TestLooksLikeName(unittest.TestCase):
    """首用引导防误吞：短任务/问句放行，真称呼才落档。"""

    @classmethod
    def setUpClass(cls):
        try:  # 导入会连带 dify_plugin SDK（需 py3.10+）；本机 3.9 环境跳过
            from tools.my_claw_tool import looks_like_name
        except ImportError:
            raise unittest.SkipTest("tools.my_claw_tool requires dify_plugin (py3.10+)")
        cls.fn = staticmethod(looks_like_name)

    def test_real_names_accepted(self):
        for s in ("boss", "小任", "张伟", "Alex", "王大明"):
            self.assertTrue(self.fn(s), s)

    def test_tasks_rejected(self):
        for s in ("帮我整理一下租赁的资料", "把上次的那个东西改一下", "查一下余额", "算个账", "写个周报", "整理资料"):
            self.assertFalse(self.fn(s), s)

    def test_questions_and_punct_rejected(self):
        for s in ("你能做什么", "在吗？", "你好，请问", "我叫小任，请多关照"):
            self.assertFalse(self.fn(s), s)

    def test_long_rejected(self):
        self.assertFalse(self.fn("一个特别特别特别特别特别特别长的名字"))


# ═══ 0.6.1: English aliases + bilingual replies ═══════════════════════════

class TestEnglishRouting(unittest.TestCase):
    def test_en_list_variants(self):
        for q in ("view memory", "Show memory", "list memories", "LIST MEMORY", "display my memories", "memory list", "please view memory"):
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "list", q)
            self.assertEqual(cmd.lang, "en", q)

    def test_en_delete_variants(self):
        cases = {"delete memory 2": 1, "forget memory 1": 0, "remove memory 3": 2, "Delete my memory 12": 11, "delete memory entry no. 4": 3}
        for q, idx in cases.items():
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "delete", q)
            self.assertEqual(cmd.index, idx, q)
            self.assertEqual(cmd.lang, "en", q)

    def test_en_edit_variants(self):
        cases = {
            "edit memory 1 to prefer concise reports": (0, "prefer concise reports"),
            "Update memory 2 to 24 months": (1, "24 months"),
            "change my memory 3 to bullet style": (2, "bullet style"),
        }
        for q, (idx, new) in cases.items():
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "edit", q)
            self.assertEqual(cmd.index, idx, q)
            self.assertEqual(cmd.new_text, new, q)

    def test_en_archive(self):
        for q in ("view archived memory", "show archived memories", "list my archived memories", "memory archive"):
            cmd = parse_memory_command(q)
            self.assertIsNotNone(cmd, q)
            self.assertEqual(cmd.action, "archive", q)

    def test_en_negatives(self):
        for q in ("do you remember my birthday", "what is memory", "memory", "delete memory", "remember this word", "how is your memory going today", "in memory of the project"):
            self.assertIsNone(parse_memory_command(q), q)

    def test_cn_still_primary_and_lang_zh(self):
        cmd = parse_memory_command("查看记忆")
        self.assertEqual((cmd.action, cmd.lang), ("list", "zh"))
        self.assertEqual(parse_memory_command("delete memory 2").lang, "en")


class TestBilingualReplies(unittest.TestCase):
    def test_en_list_reply(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        p.merge_managed({"user_facts": ["[事实] x"]})
        text = execute_memory_command(p, parse_memory_command("view memory"))
        self.assertIn("Long-term memory", text)
        self.assertIn("1. [事实] x", text)
        self.assertIn("delete memory N", text)

    def test_en_delete_and_edit_replies(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("delete memory 1"))
        self.assertIn("Deleted entry 1", reply)
        reply = execute_memory_command(p, parse_memory_command("edit memory 1 to 24 months"))
        self.assertIn("Updated entry 1", reply)
        self.assertIn("old:", reply)
        self.assertIn("new: 24 months", reply)

    def test_en_out_of_range_and_empty_archive(self):
        p = seeded_persona()
        reply = execute_memory_command(p, parse_memory_command("delete memory 9"))
        self.assertIn("no entry 9", reply)
        kv = FakeKV()
        p2 = PersonaStore(kv, app_id="a1")
        reply = execute_memory_command(p2, parse_memory_command("view archived memory"))
        self.assertIn("archive is empty", reply)

    def test_en_archive_mentions_count_in_list(self):
        kv = FakeKV()
        p = PersonaStore(kv, app_id="a1")
        p.merge_managed({"user_facts": ["[事实] x"]})
        p.write("MEMORY.archive.md", "# 归档记忆\n## 归档于 2026-08-24\n- [经历] old\n")
        text = execute_memory_command(p, parse_memory_command("view memory"))
        self.assertIn("1 archived", text)
