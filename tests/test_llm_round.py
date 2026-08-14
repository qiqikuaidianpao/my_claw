"""Dual-channel round collection tests (port of the mini_claw 1.2.1 matrix)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm import LLMRound, collect_round, normalize_visible_text, split_think_tags, visible_text


def chunk_text(text: str) -> dict:
    return {"delta": {"message": {"content": text}}}


def chunk_reasoning(text: str) -> dict:
    """Channel A chunk: dedicated reasoning_content field."""
    return {"delta": {"message": {"content": ""}, "reasoning_content": text}}


def chunk_tool(name: str = "run_skill_command") -> dict:
    return {"delta": {"message": {"tool_calls": [{"id": "c1", "function": {"name": name, "arguments": "{}"}}]}}}


def chunk_text_and_tool(text: str) -> dict:
    return {"delta": {"message": {"content": text, "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]}}}


class TestNormalizeVisibleText(unittest.TestCase):
    def test_balanced_kept(self):
        s = "<think>r</think>answer"
        self.assertEqual(normalize_visible_text(s), s)

    def test_unbalanced_open_stripped(self):
        self.assertEqual(normalize_visible_text("<think>r\nvisible"), "r\nvisible")

    def test_unbalanced_close_stripped(self):
        self.assertEqual(normalize_visible_text("head</think>tail"), "headtail")

    def test_case_insensitive(self):
        self.assertEqual(normalize_visible_text("<THINK>r</THINK>正文"), "<THINK>r</THINK>正文")
        self.assertEqual(normalize_visible_text("<Think>r\n正文"), "r\n正文")

    def test_empty_and_plain(self):
        self.assertEqual(normalize_visible_text(""), "")
        self.assertEqual(normalize_visible_text("普通正文"), "普通正文")


class TestSplitThinkTags(unittest.TestCase):
    def test_no_tags(self):
        self.assertEqual(split_think_tags("hello"), ("", "hello"))

    def test_paired(self):
        self.assertEqual(split_think_tags("<think>abc</think>body"), ("abc", "body"))

    def test_unclosed(self):
        r, b = split_think_tags("pre<think>reasoning continues")
        self.assertEqual(b, "pre")
        self.assertIn("reasoning", r)


class TestCollectRoundMatrix(unittest.TestCase):
    """The A–G chunk-arrangement matrix from the streaming design doc."""

    def test_a_normal_final(self):
        r = collect_round([chunk_text("<think>r</think>"), chunk_text("answer")])
        self.assertFalse(r.has_tool_calls)
        self.assertTrue(r.is_final)
        # channel B split: reasoning separated from body
        self.assertEqual(r.reasoning, "r")
        self.assertEqual(r.text, "answer")
        self.assertEqual(visible_text(r), "answer")

    def test_b_close_with_tool_same_chunk(self):
        r = collect_round([chunk_text("<think>r"), chunk_text_and_tool("</think>")])
        self.assertTrue(r.has_tool_calls)
        self.assertFalse(r.is_final)

    def test_c_close_after_tool(self):
        r = collect_round([chunk_text("<think>r"), chunk_tool(), chunk_text("</think>")])
        self.assertTrue(r.has_tool_calls)

    def test_d_tool_only(self):
        r = collect_round([chunk_tool(), chunk_tool()])
        self.assertTrue(r.has_tool_calls)
        self.assertEqual(r.text, "")

    def test_e_multi_round_only_final_publishes(self):
        r1 = collect_round([chunk_text("<think>a</think>"), chunk_tool()])
        r2 = collect_round([chunk_text("<think>b</think>"), chunk_tool()])
        r3 = collect_round([chunk_text("<think>c</think>"), chunk_text("done")])
        self.assertTrue(r1.has_tool_calls and r2.has_tool_calls)
        self.assertTrue(r3.is_final)
        self.assertEqual(visible_text(r3), "done")

    def test_f_final_unbalanced(self):
        r = collect_round([chunk_text("<think>推理中"), chunk_text("\n看起来像正文")])
        self.assertTrue(r.has_unbalanced_think_tags)
        self.assertNotIn("<think>", visible_text(r))

    def test_g_tags_split_across_chunks(self):
        r = collect_round([chunk_text("<think>a,"), chunk_text("b</think>"), chunk_text("答案")])
        self.assertEqual(r.reasoning, "a,b")
        self.assertEqual(r.text, "答案")


class TestDualChannelReasoning(unittest.TestCase):
    """Channel A (reasoning_content field) preferred over tag fallback."""

    def test_channel_a_only(self):
        r = collect_round([chunk_reasoning("思考过程"), chunk_text("最终答复")])
        self.assertEqual(r.reasoning, "思考过程")
        self.assertEqual(r.text, "最终答复")
        self.assertFalse(r.has_unbalanced_think_tags)

    def test_channel_a_mixed_with_tool(self):
        r = collect_round([chunk_reasoning("decide"), chunk_tool()])
        self.assertEqual(r.reasoning, "decide")
        self.assertTrue(r.has_tool_calls)

    def test_channel_a_wins_when_present(self):
        # if A present, inline tags in content are kept as raw text (not re-split)
        r = collect_round([chunk_reasoning("A"), chunk_text("body")])
        self.assertEqual(r.reasoning, "A")

    def test_nontext_parts_collected(self):
        part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}
        chunk = {"delta": {"message": {"content": [{"type": "text", "text": "hi"}, part]}}}
        r = collect_round([chunk])
        self.assertEqual(r.text, "hi")
        self.assertEqual(len(r.nontext_parts), 1)

    def test_chunk_count_and_recorder(self):
        seen: list[dict] = []
        r = collect_round([chunk_text("a"), chunk_text("b")], record_chunk=seen.append)
        self.assertEqual(r.chunk_count, 2)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
