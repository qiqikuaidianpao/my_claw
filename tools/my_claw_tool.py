"""my_claw main agent tool (thin Dify shell around the kernel)."""
from __future__ import annotations

import os
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
from core.usage import LLMUsageAccumulator
from core.util import safe_get
from core.workspace.workspace import Workspace
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin import Tool

SESSION_DIR_PREFIX = "myclaw-session-"


class MyClawTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        pending: list[ToolInvokeMessage] = []
        emitter = DifyMessageEmitter(self, pending.append)
        try:
            for _ in self._run(tool_parameters, emitter, pending):
                while pending:
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

        if not query:
            yield self.create_text_message("❌缺少 query 参数")
            return

        kv = DifyKVStorage(self.session)
        app_id = str(safe_get(self.session, "app_id") or "app")
        user_id = str(safe_get(self.session, "user_id") or getattr(self.runtime, "user_id", "") or "")
        conversation_id = str(safe_get(self.session, "conversation_id") or "")

        persona = PersonaStore(kv, app_id=app_id, user_id=user_id)
        memory = MemoryService(kv, app_id=app_id, user_id=user_id)
        history = HistoryStore(kv, conversation_id=conversation_id)

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
        )

        for f in tool_parameters.get("files") or []:
            try:
                self._save_upload(ws, f)
            except Exception as e:
                log.warning("upload_save_failed", detail=str(e))

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
