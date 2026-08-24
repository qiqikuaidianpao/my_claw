"""Clarify interaction tests (0.6.0 F1): matcher, state machine, kernel signal."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.clarify import (
    MAX_MISSES,
    PendingClarify,
    build_continuation,
    format_question,
    match_option,
)


OPTS = ["归类整理：按项目分类多份文件，输出清单", "内容提炼：提取各文件要点，汇总一页摘要", "生成文档：整理成 Word 汇报材料"]


class TestMatchOption:
    def test_bare_numeral(self):
        for reply, expect in [("1", 0), ("2", 1), ("3", 2), ("2.", 1), ("2）", 1), ("①", 0), ("②", 1), ("③", 2)]:
            assert match_option(reply, OPTS) == expect, reply

    def test_chinese_numeral(self):
        assert match_option("一", OPTS) == 0
        assert match_option("二", OPTS) == 1
        assert match_option("三", OPTS) == 2

    def test_ordinal(self):
        for reply, expect in [("第一个", 0), ("第2个", 1), ("第三条", 2), ("第3项", 2)]:
            assert match_option(reply, OPTS) == expect, reply

    def test_pick_verb(self):
        for reply, expect in [("选2", 1), ("选3", 2), ("要1", 0), ("是2号", 1)]:
            assert match_option(reply, OPTS) == expect, reply

    def test_option_text_substring(self):
        assert match_option("内容提炼", OPTS) == 1  # substring of option 2
        assert match_option("生成文档", OPTS) == 2

    def test_no_match(self):
        assert match_option("帮我改一下", OPTS) is None
        assert match_option("算了不用了", OPTS) is None
        assert match_option("", OPTS) is None
        assert match_option("你好", OPTS) is None  # too short for text match

    def test_short_reply_no_false_positive(self):
        assert match_option("好的", OPTS) is None
        assert match_option("嗯", OPTS) is None


class TestPendingClarify:
    def test_roundtrip(self):
        p = PendingClarify(original_query="帮我整理租赁资料", question="哪种理解", options=OPTS)
        p2 = PendingClarify.from_json(p.to_json())
        assert p2 is not None
        assert p2.original_query == p.original_query
        assert p2.options == OPTS
        assert p2.misses == 0

    def test_from_json_garbage(self):
        assert PendingClarify.from_json("not json") is None
        assert PendingClarify.from_json(b"\xff\xfe") is None

    def test_expiry(self):
        p = PendingClarify(original_query="q", question="?", options=OPTS, asked_at=time.time() - 25 * 3600)
        assert p.expired()
        p_fresh = PendingClarify(original_query="q", question="?", options=OPTS)
        assert not p_fresh.expired()

    def test_max_misses_constant(self):
        assert MAX_MISSES == 2


class TestFormatAndContinuation:
    def test_format(self):
        text = format_question("这个任务有几种理解", OPTS[:2])
        assert "🤔" in text
        assert "1️⃣" in text and "2️⃣" in text
        assert "3️⃣" not in text  # only 2 options
        assert "回复数字" in text

    def test_continuation(self):
        p = PendingClarify(original_query="帮我整理租赁资料", question="?", options=OPTS)
        c = build_continuation(p, 1)
        assert c.startswith("帮我整理租赁资料")
        assert "选项2" in c
        assert OPTS[1] in c


class TestAskUserTool:
    def test_registration(self):
        import core.tools.builtin  # noqa: F401
        from core.tools.registry import get

        spec = get("ask_user")
        assert spec is not None
        assert spec.visible_to_model
        assert "question" in spec.parameters["properties"]
        assert "options" in spec.parameters["properties"]

    def test_options_count_guard(self):
        import core.tools.builtin  # noqa: F401
        from core.tools.registry import get

        from core.session import SessionContext

        ctx = SessionContext(session_id="t", messages=[], workspace_root=".", skills_root=".")
        spec = get("ask_user")
        assert spec is not None
        result = spec.handler(ctx=ctx, emitter=None, question="q", options=["only one"])
        assert not result.ok
        assert "options_must_be_2_to_4" in result.content

    def test_writes_pending_and_returns_signal(self):
        import core.tools.builtin  # noqa: F401
        from core.tools.registry import get

        from core.session import SessionContext

        class FakeKV:
            def __init__(self):
                self.store = {}

            def set(self, k, v):
                self.store[k] = v

            def get(self, k):
                return self.store.get(k)

        kv = FakeKV()
        ctx = SessionContext(session_id="t", messages=[{"role": "user", "content": "帮我整理资料"}], workspace_root=".", skills_root=".")
        ctx.extra["kv_store"] = kv
        ctx.extra["original_query"] = "帮我整理资料"
        spec = get("ask_user")
        assert spec is not None
        result = spec.handler(ctx=ctx, emitter=None, question="哪种理解", options=OPTS)
        assert result.ok
        assert '"clarify_asked": true' in result.content
        from core.clarify import PENDING_KEY, PendingClarify

        p = PendingClarify.from_json(kv.get(PENDING_KEY))
        assert p is not None
        assert p.original_query == "帮我整理资料"
        assert p.options == OPTS


class TestKernelClarifySignal:
    def test_kernel_stops_on_clarify(self):
        """The kernel agent loop must terminate when ask_user returns its marker."""
        from core.kernel import AgentKernel
        from core.ports import MessageEmitter
        from core.session import SessionContext

        from core.llm import LLMRound

        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def invoke_round(self, system, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    tc = {"id": "c1", "function": {"name": "ask_user", "arguments": '{"question":"哪种","options":["A方案","B方案"]}'}}
                    return LLMRound(tool_calls=(tc,))
                return LLMRound(text="should not reach")

        emitted: list[str] = []

        class FakeEmitter(MessageEmitter):
            def text(self, chunk):
                emitted.append(chunk)

            def file(self, *a, **kw):
                pass

        ctx = SessionContext(session_id="t", messages=[{"role": "user", "content": "整理资料"}], workspace_root=".", skills_root=".")
        kernel = AgentKernel(llm=FakeLLM(), emitter=FakeEmitter())
        list(kernel.run_iter(ctx))
        assert ctx.final_text_emitted
        assert "🤔" in ctx.final_text
        assert "1️⃣ A方案" in ctx.final_text
        assert "2️⃣ B方案" in ctx.final_text
