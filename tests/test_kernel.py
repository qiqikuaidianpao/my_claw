"""AgentKernel orchestration tests with fake LLM/emitter."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.kernel import AgentKernel
from core.llm import LLMRound
from core.session import SessionContext, LoopGuard
from core.tools import registry as reg


class FakeEmitter:
    def __init__(self):
        self.texts: list[str] = []
        self.blobs: list[tuple] = []
        self.variables: list[tuple] = []

    def text(self, chunk: str) -> None:
        self.texts.append(chunk)

    def blob(self, data: bytes, *, mime_type: str, filename: str) -> None:
        self.blobs.append((data, mime_type, filename))

    def variable(self, key: str, value: dict) -> None:
        self.variables.append((key, value))

    def joined(self) -> str:
        return "".join(self.texts)


class ScriptedLLM:
    """Returns queued rounds in order."""

    def __init__(self, rounds: list[LLMRound]):
        self.rounds = list(rounds)
        self.calls = 0

    def invoke_round(self, *, system, messages, tools=None) -> LLMRound:
        self.calls += 1
        return self.rounds.pop(0)

    def invoke_text(self, *, system, messages) -> str:
        return "ok"


def round_tool(name="echo", args="{}") -> LLMRound:
    return LLMRound(
        text="",
        tool_calls=({"id": "c1", "function": {"name": name, "arguments": args}},),
    )


def round_final(text: str) -> LLMRound:
    return LLMRound(text=text)


class TestAgentKernel(unittest.TestCase):
    def setUp(self):
        reg.clear()
        self.emitter = FakeEmitter()

    def _kernel(self, llm) -> AgentKernel:
        return AgentKernel(llm=llm, emitter=self.emitter)

    def _ctx(self, **kw) -> SessionContext:
        return SessionContext(session_id="s1", messages=[{"role": "user", "content": "hi"}], **kw)

    def test_final_round_published(self):
        llm = ScriptedLLM([round_final("搞定了！答复正文")])
        ctx = self._kernel(llm).run(self._ctx())
        self.assertEqual(ctx.final_text, "搞定了！答复正文")
        self.assertIn("搞定了！", self.emitter.joined())
        self.assertTrue(ctx.final_text_emitted)

    def test_intermediate_round_publishes_nothing(self):
        @reg.tool("echo", description="echo", parameters={"type": "object", "properties": {}}, progress="✅正在执行 echo…")
        def echo(ctx, emitter, **kw):
            return reg.ToolResult(content='{"ok": true}')

        llm = ScriptedLLM([round_tool(), round_final("最终答复")])
        ctx = self._kernel(llm).run(self._ctx())
        # user saw only the progress line + final answer, never intermediate model text
        joined = self.emitter.joined()
        self.assertIn("最终答复", joined)
        self.assertIn("✅正在执行 echo…", joined)
        self.assertEqual(llm.calls, 2)
        # tool observation fed back into history, then final assistant answer
        roles = [m["role"] for m in ctx.messages]
        self.assertIn("tool", roles)
        self.assertEqual(roles[-1], "assistant")

    def test_unknown_tool_fed_back_not_crash(self):
        llm = ScriptedLLM([round_tool(name="nope"), round_final("ok")])
        ctx = self._kernel(llm).run(self._ctx())
        tool_obs = [m for m in ctx.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_obs), 1)
        self.assertIn("unknown_tool", tool_obs[0]["content"])
        # assistant tool_calls message precedes the tool observation
        i_assistant = next(i for i, m in enumerate(ctx.messages) if m.get("role") == "assistant" and m.get("tool_calls"))
        i_tool = ctx.messages.index(tool_obs[0])
        self.assertLess(i_assistant, i_tool)

    def test_loop_detection_stops(self):
        calls = {"n": 0}

        @reg.tool("spin", description="spin", parameters={"type": "object", "properties": {}})
        def spin(ctx, emitter, **kw):
            calls["n"] += 1
            return reg.ToolResult(content='{"n": %d}' % calls["n"])

        rounds = [round_tool(name="spin") for _ in range(25)]
        llm = ScriptedLLM(rounds)
        ctx = self._ctx()
        ctx.loop_guard = LoopGuard(window=30, warn_after=3, stop_after=5, no_progress_limit=99)
        self._kernel(llm).run(ctx)
        self.assertTrue(calls["n"] < 25, "loop breaker should stop early")
        self.assertIn("循环", ctx.final_text)

    def test_empty_responses_retry_then_stop(self):
        llm = ScriptedLLM([LLMRound(), LLMRound(), LLMRound(), LLMRound()])
        ctx = self._kernel(llm).run(self._ctx())
        self.assertEqual(ctx.empty_responses, 3)
        self.assertIn("空响应", ctx.final_text)

    def test_timeout_breaks_loop(self):
        llm = ScriptedLLM([round_tool() for _ in range(30)])
        ctx = self._ctx(timeout_seconds=1)
        ctx.started_at = time.time() - 10  # already past deadline
        self._kernel(llm).run(ctx)
        self.assertIn("超时", ctx.final_text)
        self.assertEqual(llm.calls, 0)

    def test_required_args_validation(self):
        @reg.tool("need_arg", description="d", parameters={"type": "object", "properties": {"x": {"type": "string"}}}, required=("x",))
        def need_arg(ctx, emitter, x):
            return reg.ToolResult(content='{"x": "%s"}' % x)

        llm = ScriptedLLM([
            LLMRound(tool_calls=({"id": "c1", "function": {"name": "need_arg", "arguments": "{}"}},)),
            round_final("ok"),
        ])
        ctx = self._kernel(llm).run(self._ctx())
        tool_obs = [m for m in ctx.messages if m.get("role") == "tool"][0]["content"]
        self.assertIn("missing required", tool_obs)


class TestRegistry(unittest.TestCase):
    def test_duplicate_rejected(self):
        reg.clear()
        @reg.tool("dup", description="a", parameters={})
        def a(ctx, emitter):
            return reg.ToolResult(content="{}")
        with self.assertRaises(ValueError):
            @reg.tool("dup", description="b", parameters={})
            def b(ctx, emitter):
                return reg.ToolResult(content="{}")

    def test_hidden_tool_not_listed(self):
        reg.clear()
        @reg.tool("hidden", description="h", parameters={}, visible_to_model=False)
        def h(ctx, emitter):
            return reg.ToolResult(content="{}")
        names = [s.name for s in reg.all_specs()]
        self.assertNotIn("hidden", names)
        self.assertIsNotNone(reg.get("hidden"))


class TestSession(unittest.TestCase):
    def test_artifact_registration(self):
        ctx = SessionContext(session_id="s")
        ctx.register_artifact("out/合同.docx")
        art = ctx.artifacts["out/合同.docx"]
        self.assertEqual(art.filename, "合同.docx")
        self.assertIn("wordprocessingml", art.mime_type)

    def test_loop_guard_no_progress(self):
        g = LoopGuard(no_progress_limit=3)
        for _ in range(3):
            warned, stop = g.observe("same", "same")
        self.assertTrue(stop)

    def test_snapshot(self):
        ctx = SessionContext(session_id="s")
        snap = ctx.snapshot_state()
        self.assertIn("messages", snap)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestClarifyHistoryInKernel(unittest.TestCase):
    """0.6.1: ask_user 拦截时的历史查询路径与工具隐藏过滤。"""

    def test_hidden_tools_filters_schema(self):
        import importlib

        import core.tools.builtin as _b  # 前序测试可能 clear 过注册表，重载以重新注册
        importlib.reload(_b)
        from core.kernel import AgentKernel
        from core.session import SessionContext

        k = AgentKernel(llm=None, emitter=None)
        ctx = SessionContext(session_id="s", messages=[])
        names_all = [s["function"]["name"] for s in k._tool_schemas(ctx)]
        self.assertIn("ask_user", names_all)
        ctx.extra["hidden_tools"] = {"ask_user"}
        names_hidden = [s["function"]["name"] for s in k._tool_schemas(ctx)]
        self.assertNotIn("ask_user", names_hidden)
        self.assertGreater(len(names_hidden), 5)  # 其余工具不受影响

    def test_auto_continue_feeds_result_and_continues(self):
        """历史高频同选：ask_user 的结果被替换为历史选择并继续本轮（不挂起）。"""
        import json as _json

        import core.tools.builtin  # noqa: F401
        from core.kernel import AgentKernel
        from core.session import SessionContext
        from tests.test_clarify import FakeKV2
        from core.clarify import ClarifyHistory

        hist = ClarifyHistory(FakeKV2(), "a", "u")
        hist.record("把上次的那个东西改成另一个人那样", ["改台账", "改文件", "重做"], 1)
        hist.record("把上次的那个东西改成另一个样子", ["改台账", "改文件", "重做"], 1)

        captured = {}

        class _Em:
            def text(self, chunk):
                captured["emitted"] = captured.get("emitted", "") + chunk

        class _Round:
            has_tool_calls = True

        class _LLM:
            def __init__(self):
                self.calls = 0

        class _Kernel(AgentKernel):
            def __init__(self):
                super().__init__(llm=_LLM(), emitter=_Em(), compactor=None)

            def _execute_tools(self, round_, ctx):
                # 直接模拟 ask_user 被调用后的拦截分支
                from core.clarify import format_question

                question = "把上次的那个东西改成另一个人那样"
                options = ["改台账", "改文件", "重做"]
                history = ctx.extra.get("clarify_history")
                lookup = history.lookup(question, options) if history else {}
                assert lookup.get("auto") == 1, lookup
                chosen = options[lookup["auto"]]
                self._emit_text("🧠 类似情况你之前都选「%s」，这次直接按它继续\n" % chosen)
                captured["continued"] = True  # continue 而非 break
                return None

        ctx = SessionContext(session_id="s", messages=[])
        ctx.extra["clarify_history"] = hist
        k = _Kernel()
        k._execute_tools(_Round(), ctx)
        self.assertTrue(captured.get("continued"))
        self.assertIn("改文件", captured.get("emitted", ""))
        self.assertNotIn("1️⃣", captured.get("emitted", ""))  # 没有弹选项
