"""Onboarding / approval state machine tests (pure logic via stubs)."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# stub dify_plugin so tools module imports without SDK
dp = types.ModuleType("dify_plugin")
ents = types.ModuleType("dify_plugin.entities")
tool = types.ModuleType("dify_plugin.entities.tool")
tool.ToolInvokeMessage = type("ToolInvokeMessage", (), {})
ents.tool = tool
dp.entities = ents
dp.Tool = type("Tool", (), {})
sys.modules.setdefault("dify_plugin", dp)
sys.modules.setdefault("dify_plugin.entities", ents)
sys.modules.setdefault("dify_plugin.entities.tool", tool)
# adapters.dify.llm_client 需要的message实体stub
msg = types.ModuleType("dify_plugin.entities.model.message")
for cls in ("SystemPromptMessage", "UserPromptMessage", "AssistantPromptMessage", "ToolPromptMessage", "PromptMessageTool"):
    setattr(msg, cls, type(cls, (), {}))
model = types.ModuleType("dify_plugin.entities.model")
model.message = msg
dp.model = model
sys.modules.setdefault("dify_plugin.entities.model", model)
sys.modules.setdefault("dify_plugin.entities.model.message", msg)

from core.memory.persona import PersonaStore
from core.tools.builtin import SENSITIVE_BINS


class FakeKV:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class FakeToolHost:
    """Drives MyClawTool's onboarding/approval phase logic without Dify."""

    @staticmethod
    def messages(gen):
        return [m for m in gen]

    @classmethod
    def run_onboarding(cls, kv, persona, query):
        from tools.my_claw_tool import MyClawTool

        msgs = []

        class Capture(MyClawTool):
            def create_text_message(self, text):
                return text

        # _onboarding_phase 是生成器，直接借用（无需Tool实例状态）
        gen = MyClawTool._onboarding_phase(
            None,
            kv,
            persona,
            query,
        )
        # 绑定create_text_message的简版：用闭包替换
        out = []
        for m in gen:
            out.append(m)
        return out

    @staticmethod
    def parse_approval(query):
        """复制_approval_phase的裁决映射做单测。"""
        q = query.strip()
        if q in ("1", "1.", "允许", "本次允许", "yes"):
            return "once"
        if q in ("2", "2.", "总是允许", "always"):
            return "always"
        if q in ("3", "3.", "拒绝", "deny", "no"):
            return "deny"
        return None


class TestOnboarding(unittest.TestCase):
    def test_first_use_asks_name(self):
        kv = FakeKV()
        persona = PersonaStore(kv, app_id="a")
        # 通过运行phase验证：使用Capture类提供create_text_message
        from tools.my_claw_tool import MyClawTool

        class C:
            create_text_message = staticmethod(lambda t: t)

        # 直接以未绑定方式调用（self仅用create_text_message）
        gen = MyClawTool._onboarding_phase(C(), kv, persona, "帮我写个文件")
        msgs = list(gen)
        self.assertTrue(any("称呼" in m for m in msgs))
        self.assertEqual(kv.get("claw:onboarding:stage"), b"1")

    def test_second_round_saves_name(self):
        kv = FakeKV()
        persona = PersonaStore(kv, app_id="a")
        kv.set("claw:onboarding:stage", b"1")
        from tools.my_claw_tool import MyClawTool

        class C:
            create_text_message = staticmethod(lambda t: t)

        msgs = list(MyClawTool._onboarding_phase(C(), kv, persona, "任聪聪"))
        self.assertTrue(any("任聪聪" in m for m in msgs))
        self.assertIn("任聪聪", persona.read("USER.md"))

    def test_reset_keyword(self):
        kv = FakeKV()
        persona = PersonaStore(kv, app_id="a")
        persona.write("USER.md", "称呼：老板")
        from tools.my_claw_tool import MyClawTool

        class C:
            create_text_message = staticmethod(lambda t: t)

        msgs = list(MyClawTool._onboarding_phase(C(), kv, persona, "重置人格"))
        self.assertTrue(any("重新开始" in m for m in msgs))
        self.assertEqual(persona.read("USER.md"), "")

    def test_done_state_passes_through(self):
        kv = FakeKV()
        persona = PersonaStore(kv, app_id="a")
        persona.write("USER.md", "称呼：老板")
        kv.set("claw:onboarding:stage", b"done")
        from tools.my_claw_tool import MyClawTool

        class C:
            create_text_message = staticmethod(lambda t: t)

        msgs = list(MyClawTool._onboarding_phase(C(), kv, persona, "干活"))
        self.assertEqual(msgs, [])


class TestApproval(unittest.TestCase):
    def test_verdict_mapping(self):
        p = FakeToolHost.parse_approval
        self.assertEqual(p("1"), "once")
        self.assertEqual(p("2"), "always")
        self.assertEqual(p("3"), "deny")
        self.assertIsNone(p("随便说说"))

    def test_sensitive_set(self):
        for b in ("curl", "pip", "npm", "git"):
            self.assertIn(b, SENSITIVE_BINS)
        self.assertNotIn("python3", SENSITIVE_BINS)

    def test_approval_check_pending_flow(self):
        kv = FakeKV()
        from tools.my_claw_tool import MyClawTool

        class C:
            create_text_message = staticmethod(lambda t: t)

        from tools.my_claw_tool import MyClawTool as MC

        class Host(MC):
            def __init__(self):
                pass

        check = Host._make_approval_check(Host(), kv, "q", "u1", "a1", "c1")
        self.assertEqual(check(["curl", "https://x"], 30), "pending")
        pending = kv.get("claw:approval:c1:pending")
        self.assertIsNotNone(pending)
        # always授权后放行
        import json

        kv.set("claw:approval:a1:user:u1:grants", json.dumps(["curl"]).encode())
        self.assertEqual(check(["curl", "https://x"], 30), "allowed")


class TestApprovalShortCircuit(unittest.TestCase):
    def test_kernel_short_circuits_on_approval_required(self):
        """审批短路：工具返回approval_required时，内核直接向用户挂起并终止。"""
        from core.kernel import AgentKernel
        from core.llm import LLMRound
        from core.session import SessionContext
        from core.tools import registry as reg

        reg.clear()

        @reg.tool("sensitive", description="s", parameters={"type": "object", "properties": {}})
        def sensitive(ctx, emitter, **kw):
            return reg.ToolResult(content='{"approval_required": true, "command": ["curl", "x"]}')

        emitted: list[str] = []

        class E:
            def text(self, chunk):
                emitted.append(chunk)

            def blob(self, *a, **k):
                pass

            def variable(self, *a, **k):
                pass

        round1 = LLMRound(tool_calls=({"id": "c1", "function": {"name": "sensitive", "arguments": "{}"}},))
        llm = type("L", (), {"invoke_round": staticmethod(lambda **kw: round1), "invoke_text": staticmethod(lambda **kw: "")})()
        ctx = SessionContext(session_id="s", messages=[{"role": "user", "content": "q"}])
        AgentKernel(llm=llm, emitter=E()).run(ctx)
        joined = "".join(emitted)
        self.assertIn("需要你审批", joined)
        self.assertIn("1=本次允许", joined)
        self.assertTrue(ctx.final_text_emitted)
        self.assertEqual(len(ctx.rounds), 1)  # 审批短路后不再进入下一轮


class TestSkillManagementRouting(unittest.TestCase):
    def test_business_message_mentioning_skills_stays_in_agent_loop(self):
        from tools.my_claw_tool import MyClawTool

        self.assertIsNone(MyClawTool._skill_management_command("发送润工作群通知：技能热插拔功能演示中"))
        self.assertIsNone(MyClawTool._skill_management_command("总结技能管理方案并生成汇报"))

    def test_five_management_commands_are_routed(self):
        from tools.my_claw_tool import MyClawTool

        cases = {
            "查看技能": ("list", 0),
            "新增技能": ("install", 0),
            "删除技能2": ("remove", 2),
            "下载技能3": ("download", 3),
            "依赖安装": ("dependencies", 0),
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(MyClawTool._skill_management_command(query), expected)

    def test_management_word_order_variants_are_routed(self):
        from tools.my_claw_tool import MyClawTool

        self.assertEqual(MyClawTool._skill_management_command("帮我看看技能列表"), ("list", 0))
        self.assertEqual(MyClawTool._skill_management_command("把第4个技能删除掉"), ("remove", 4))
        self.assertEqual(MyClawTool._skill_management_command("请安装缺少的依赖"), ("dependencies", 0))

    def test_management_command_short_circuits_to_manager(self):
        from tools.my_claw_tool import MyClawTool

        captured = {}

        class FakeManager:
            def __init__(self, runtime, session):
                captured["host"] = (runtime, session)

            def _invoke(self, parameters):
                captured["parameters"] = parameters
                yield "managed"

        host = MyClawTool.__new__(MyClawTool)
        host.runtime = "runtime"
        host.session = "session"
        with patch("tools.skill_manager_tool.SkillManagerTool", FakeManager):
            result = list(host._run({"query": "删除技能2", "skills_root": "skills"}, None, []))

        self.assertEqual(result, ["managed"])
        self.assertEqual(captured["host"], ("runtime", "session"))
        self.assertEqual(captured["parameters"]["action"], "remove")
        self.assertEqual(captured["parameters"]["index"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
