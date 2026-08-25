"""my_claw main agent tool (thin Dify shell around the kernel)."""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Generator
from typing import Any

import core.tools.builtin  # noqa: F401  — registers built-in tools
from adapters.dify.emitter import DifyKVStorage, DifyMessageEmitter
from adapters.dify.llm_client import DifyLLMClient
from core import log
from core.context import ContextManager, HistoryStore
from core.kernel import AgentKernel
from core.memory.persona import PersonaStore
from core.memory.service import MemoryService
from core.prompt import build_system_prompt
from core.session import SessionContext
from core.skills.commands import parse_skill_management_command
from core.usage import LLMUsageAccumulator
from core.util import safe_get
from core.workspace.workspace import Workspace
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin import Tool

SESSION_DIR_PREFIX = "myclaw-session-"

_TASKY_PREFIX_RE = re.compile(
    r"^(帮我|请|给我|麻烦|把|想|我要|我想|你|您|怎么|什么|为什么|为啥|能否|可以|会不会|是不是|查|看|算|写|做|生成|整理|删除|修改|安装|下载|上传|翻译|总结|分析|对比|规划|列|找|发|建|创建|重置|新增|停止|继续)"
)
_SENTENCE_PUNCT_RE = re.compile(r"[，。,.!?？!：:;；、\s]")


def looks_like_name(s: str) -> bool:
    """称呼判定：短、无标点/空格、非任务/问句开头。放行可疑输入优于误吞任务。"""
    s = s.strip()
    if len(s) < 2 or len(s) > 12:
        return False
    if _SENTENCE_PUNCT_RE.search(s):
        return False
    if _TASKY_PREFIX_RE.match(s):
        return False
    return True


class MyClawTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        pending: list[ToolInvokeMessage] = []
        emitter = DifyMessageEmitter(self, pending.append)
        try:
            for msg in self._run(tool_parameters, emitter, pending):
                if msg is not None:  # _run直接产出的消息（onboarding/审批/错误）
                    yield msg
                while pending:  # emitter缓冲的消息（agent正文/进度）
                    yield pending.pop(0)
        except Exception as e:
            log.error("tool_run_crashed", detail=str(e))
            yield self.create_text_message(f"❌ my_claw 执行异常：{e}")
        finally:
            while pending:
                yield pending.pop(0)

    def _run(self, tool_parameters: dict[str, Any], emitter, pending: list):
        model = tool_parameters.get("model")
        query = str(tool_parameters.get("query") or "").strip()
        timeout_seconds = int(tool_parameters.get("timeout_seconds") or 600)
        skills_root = str(tool_parameters.get("skills_root") or "") or os.environ.get("SKILLS_ROOT") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"
        )
        user_instructions = str(tool_parameters.get("system_prompt") or "")
        memory_turns = int(tool_parameters.get("memory_turns") or 12)
        # clarify_enabled 默认开；编排者可关掉 ask_user（隐藏工具+提示词切换到直接执行）
        clarify_enabled = str(tool_parameters.get("clarify_enabled")).lower() not in ("false", "0", "no", "none")

        if not query:
            yield self.create_text_message("❌缺少 query 参数")
            return

        management_command = self._skill_management_command(query)
        if management_command is not None:
            from tools.skill_manager_tool import SkillManagerTool

            action, index = management_command
            manager = SkillManagerTool(self.runtime, self.session)
            yield from manager._invoke(
                {
                    "action": action,
                    "index": index,
                    "files": tool_parameters.get("files"),
                    "skills_root": skills_root,
                }
            )
            return

        kv = DifyKVStorage(self.session)
        app_id = str(safe_get(self.session, "app_id") or "app")
        user_id = str(safe_get(self.session, "user_id") or getattr(self.runtime, "user_id", "") or "")
        conversation_id = str(safe_get(self.session, "conversation_id") or "")

        persona = PersonaStore(kv, app_id=app_id, user_id=user_id)
        memory = MemoryService(kv, app_id=app_id, user_id=user_id)
        history = HistoryStore(kv, conversation_id=conversation_id)

        # ── 前置阶段1：执行审批应答（上一轮挂起的敏感命令） ──
        if str(tool_parameters.get("exec_approval_enabled") or "").lower() in ("true", "1", "yes"):
            handled = yield from self._approval_phase(kv, query, user_id, app_id, conversation_id)
            if handled:
                return

        # ── 前置阶段2：记忆管理命令（查看/删除/修改，确定性路由不耗LLM；
        #    放在 onboarding 之前，避免首用时被当作称呼吞掉） ──
        from core.memory.commands import execute_memory_command, parse_memory_command

        mem_cmd = parse_memory_command(query)
        if mem_cmd is not None:
            yield self.create_text_message(execute_memory_command(persona, mem_cmd))
            return

        # ── 前置阶段3：人格引导（首用问称呼；"重置人格"清空） ──
        if str(tool_parameters.get("skip_onboarding") or "").lower() not in ("true", "1", "yes"):
            handled = yield from self._onboarding_phase(kv, persona, query)
            if handled:
                return

        # ── 前置阶段4：澄清应答（上一轮 ask_user 挂起的选项匹配） ──
        from core.clarify import PENDING_KEY, PendingClarify, build_continuation, match_option

        raw_pending = kv.get(PENDING_KEY)
        if raw_pending and not clarify_enabled:
            kv.set(PENDING_KEY, b"")  # 澄清已关停，清掉历史挂起
            raw_pending = None
        if raw_pending:
            pending_clarify = PendingClarify.from_json(raw_pending)
            if pending_clarify is None or pending_clarify.expired() or not pending_clarify.options:
                kv.set(PENDING_KEY, b"")
            else:
                hit = match_option(query, pending_clarify.options)
                if hit is not None:
                    kv.set(PENDING_KEY, b"")
                    query = build_continuation(pending_clarify, hit)
                else:
                    pending_clarify.misses += 1
                    if pending_clarify.misses >= 2:
                        kv.set(PENDING_KEY, b"")  # 用户换话题，丢弃
                    else:
                        kv.set(PENDING_KEY, pending_clarify.to_json().encode("utf-8"))
                        # 带着澄清上下文继续主循环，LLM 自然处理
                        query = (
                            f"{query}\n（系统注：上一轮你问了澄清问题：{pending_clarify.question}，"
                            f"用户回复了上面这句。若这不是选择选项，把它当作新的用户输入处理。）"
                        )

        # persisted session workspace per conversation
        session_dir = self._session_dir(kv, conversation_id)
        ws = Workspace(session_dir)
        ws.cleanup_old_sessions()

        usage = LLMUsageAccumulator()
        llm = DifyLLMClient(self.session, model or {}, usage_meter=usage)

        # conversation start: replay recent turns for continuity.
        # In stateless contexts (workflow runs without a conversation id) the
        # history bucket is shared by every run, so replay would bloat the
        # prompt with unrelated turns — disable it there.
        prior_turns = history.load()[-memory_turns:] if conversation_id else []
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": t["u"]} if i % 2 == 0 else {"role": "assistant", "content": t["a"]}
            for i, t in enumerate(prior_turns)
        ]
        messages.append({"role": "user", "content": query})

        ctx = SessionContext(
            session_id=session_dir,
            messages=messages,
            workspace_root=session_dir,
            skills_root=skills_root,
            timeout_seconds=timeout_seconds,
        )
        ctx.system_prompt = build_system_prompt(
            skills_root=skills_root,
            project_context=self._memory_context(persona, memory),
            user_instructions=user_instructions,
            clarify_enabled=clarify_enabled,
        )

        for f in tool_parameters.get("files") or []:
            try:
                self._save_upload(ws, f)
            except Exception as e:
                log.warning("upload_save_failed", detail=str(e))

        ctx.extra["persona"] = persona
        ctx.extra["kv_store"] = kv  # ask_user 澄清工具写 pending 用
        ctx.extra["original_query"] = query
        if not clarify_enabled:
            ctx.extra["hidden_tools"] = {"ask_user"}  # 编排者关停澄清：模型根本看不到该工具
        if str(tool_parameters.get("exec_approval_enabled") or "").lower() in ("true", "1", "yes"):
            ctx.extra["approval_check"] = self._make_approval_check(kv, query, user_id, app_id, conversation_id)

        compactor = ContextManager(
            llm=llm,
            persona_merge=persona.merge_managed,
            budget_tokens=int(tool_parameters.get("compaction_max_prompt_tokens") or 12000),
        )
        kernel = AgentKernel(llm=llm, emitter=emitter, compactor=compactor.compact_if_needed)
        try:
            yield from kernel.run_iter(ctx)
        finally:
            # persist memory + history even on failure (best effort)
            try:
                if conversation_id:
                    history.append(query, ctx.final_text)
                memory.append_digest(query, ctx.final_text)
                memory.gc()
                # E5 睡眠整理：每天首轮提取昨日 digest；条目超限时触发"做梦"整理
                from core.memory.consolidate import daily_extract, maybe_consolidate

                if daily_extract(memory, persona, llm):
                    note = maybe_consolidate(persona, llm)
                    if note:
                        emitter.text(note)
            except Exception as e:
                log.warning("memory_persist_failed", detail=str(e))

        for rel, data, mime, filename in ws.read_artifacts(ctx):
            yield self.create_blob_message(blob=data, meta={"mime_type": mime, "filename": filename})

        usage_payload = usage.payload()
        yield self.create_variable_message("llm_usage", usage_payload)
        if str(tool_parameters.get("show_usage_text") or "").lower() in ("true", "1", "yes"):
            yield self.create_text_message(usage.format_text(usage_payload))

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _skill_management_command(query: str) -> tuple[str, int] | None:
        return parse_skill_management_command(query)

    @staticmethod
    def _approval_keys(kv: DifyKVStorage, user_id: str, app_id: str, conversation_id: str) -> tuple[str, str]:
        pending_key = f"claw:approval:{conversation_id or 'anonymous'}:pending"
        grants_key = f"claw:approval:{app_id}:user:{user_id or 'global'}:grants"
        return pending_key, grants_key

    def _make_approval_check(self, kv: DifyKVStorage, query: str, user_id: str, app_id: str, conversation_id: str):
        def check(argv: list[str], timeout: int) -> str:
            import json as _json

            pending_key, grants_key = self._approval_keys(kv, user_id, app_id, conversation_id)
            raw = kv.get(grants_key)
            grants = []
            if raw:
                try:
                    grants = _json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception:
                    grants = []
            exe = (argv[0].rsplit("/", 1)[-1] or "").rsplit(chr(92), 1)[-1].lower()
            if exe in grants:
                return "allowed"
            kv.set(pending_key, _json.dumps({"argv": argv, "timeout": timeout}, ensure_ascii=False).encode("utf-8"))
            return "pending"

        return check

    def _approval_phase(self, kv: DifyKVStorage, query: str, user_id: str, app_id: str, conversation_id: str):
        """用户回复 1/2/3（或 允许/总是/拒绝）时裁决挂起的敏感命令。"""
        import json as _json
        import subprocess as _sp

        pending_key, grants_key = self._approval_keys(kv, user_id, app_id, conversation_id)
        raw = kv.get(pending_key)
        if not raw:
            return False
        try:
            pending = _json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            kv.set(pending_key, b"")
            return False
        q = query.strip()
        verdict = None
        if q in ("1", "1.", "允许", "本次允许", "yes"):
            verdict = "once"
        elif q in ("2", "2.", "总是允许", "always"):
            verdict = "always"
        elif q in ("3", "3.", "拒绝", "deny", "no"):
            verdict = "deny"
        if verdict is None:
            # 无关输入：保留挂起并提示
            yield self.create_text_message("⏳有一条命令等待审批：" + " ".join(pending.get("argv", [])) + "\n回复 1=本次允许 2=总是允许 3=拒绝")
            return True
        kv.set(pending_key, b"")
        if verdict == "deny":
            yield self.create_text_message("已拒绝执行该命令。如需其他帮助请继续说。")
            return True
        if verdict == "always":
            exe = (pending["argv"][0].rsplit("/", 1)[-1] or "").rsplit(chr(92), 1)[-1].lower()
            raw_g = kv.get(grants_key)
            grants = []
            if raw_g:
                try:
                    grants = _json.loads(raw_g.decode("utf-8", errors="ignore"))
                except Exception:
                    grants = []
            if exe not in grants:
                grants.append(exe)
            kv.set(grants_key, _json.dumps(grants).encode("utf-8"))
        try:
            proc = _sp.run(pending["argv"], capture_output=True, text=True, timeout=min(int(pending.get("timeout") or 120), 300))
            out = (proc.stdout or "")[-3000:]
            err = (proc.stderr or "")[-1000:]
            yield self.create_text_message("✅已按审批执行：" + " ".join(pending["argv"]) + "\n退出码 " + str(proc.returncode) + "\n" + (out or err or "(无输出)"))
        except Exception as e:
            yield self.create_text_message("❌审批后执行失败：" + str(e))
        return True

    def _onboarding_phase(self, kv: DifyKVStorage, persona: PersonaStore, query: str):
        """两段式人格引导：首用问称呼，次轮落档并继续正常任务。"""
        # stage 按 app+user 隔离——不同应用/访客的引导互不串台
        stage_key = f"claw:onboarding:{persona.app_id}:user:{persona.user_id}:stage"
        raw = kv.get(stage_key)
        stage = raw.decode("utf-8", errors="ignore").strip() if raw else ""
        if "重置人格" in query or "reset persona" in query.lower():
            persona.reset()
            kv.set(stage_key, b"1")
            yield self.create_text_message("已清空人格与记忆，重新开始。请问怎么称呼你？")
            return True
        user_doc = persona.read("USER.md").strip()
        if stage == "1" and not user_doc and len(query.strip()) <= 16:
            name = query.strip().strip("，。！! ")
            # 问候语/问句不是称呼——再问一次而非误存
            greetings = {"在吗", "在不在", "你好", "您好", "嗨", "hi", "hello", "你好呀", "哈喽", "ok", "好的"}
            if name.lower() in greetings or name.endswith(("?", "？")):
                yield self.create_text_message("我还在的～不过还想确认一下：怎么称呼你？（直接回复称呼即可）")
                return True
            if not looks_like_name(name):
                # 短任务被当成称呼吞掉是糟糕体验：放行正常处理，称呼留待之后
                return False
            persona.write("USER.md", "称呼：" + name)
            persona.write("IDENTITY.md", "# 身份\nmy_claw，" + name + "的智能办公助手。可靠、简洁、执行导向。")
            kv.set(stage_key, b"done")
            yield self.create_text_message("好的，" + name + "！我已记住你的称呼，随时吩咐任务。")
            return True
        if not user_doc and stage != "1":
            kv.set(stage_key, b"1")
            yield self.create_text_message("你好，我是 my_claw 🦞 首次见面——请问怎么称呼你？（回复称呼后我们直接开工）")
            return True
        return False


    @staticmethod
    def _session_dir(kv: DifyKVStorage, conversation_id: str) -> str:
        key = f"claw:session_dir:{conversation_id or 'anonymous'}"
        raw = kv.get(key)
        if raw:
            path = raw.decode("utf-8", errors="ignore").strip()
            if path and os.path.isdir(path):
                return path
        base = os.environ.get("TEMP") or "/tmp"
        path = os.path.join(base, f"{SESSION_DIR_PREFIX}{uuid.uuid4().hex[:12]}")
        try:
            kv.set(key, path.encode("utf-8"))
        except Exception as e:
            log.warning("session_dir_persist_failed", detail=str(e))
        return path

    @staticmethod
    def _memory_context(persona: PersonaStore, memory: MemoryService) -> str:
        parts: list[str] = []
        persona_ctx = persona.build_context()
        if persona_ctx:
            parts.append(persona_ctx)
        digests = memory.recent_context()
        if digests:
            parts.append("[近期对话记录]\n" + digests)
        return "\n\n".join(parts)

    def _save_upload(self, ws: Workspace, file_obj: Any) -> str:
        url = safe_get(file_obj, "url")
        filename = str(safe_get(file_obj, "filename") or "upload.bin")
        if not url:
            raise ValueError("upload without url")
        from core.net import download_file_bytes

        data = download_file_bytes(str(url))
        rel = f"uploads/{filename}"
        path = ws.resolve(rel)
        with open(path, "wb") as f:
            f.write(data)
        return rel
