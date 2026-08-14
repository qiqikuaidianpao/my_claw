"""Dify adapter: LLMClient implementation over dify_plugin's model session.

Translates platform-neutral kernel messages (plain dicts) into SDK
PromptMessage entities and buffers each streaming call into an LLMRound via
core.llm.collect_round. All SDK shape-handling lives here — the kernel stays
clean.
"""
from __future__ import annotations

from typing import Any

from dify_plugin.entities.model.message import (
    AssistantPromptMessage,
    SystemPromptMessage,
    ToolPromptMessage,
    UserPromptMessage,
)

from core import log
from core.errors import LLMParseError
from core.llm import LLMRound, collect_round
from core.util import safe_get, split_message_content


class DifyLLMClient:
    """Implements core.ports.LLMClient on top of a plugin Tool session."""

    def __init__(self, session: Any, model_config: dict[str, Any], usage_meter: Any | None = None) -> None:
        self.session = session
        self.model_config = model_config
        self.usage_meter = usage_meter

    # ── ports.LLMClient ──────────────────────────────────────────────────

    def invoke_round(self, *, system: str, messages: list[Any], tools: list[dict[str, Any]] | None = None) -> LLMRound:
        prompt_messages = self._to_prompt_messages(system, messages)
        sdk_tools = self._to_sdk_tools(tools)
        try:
            try:
                response = self.session.model.llm.invoke(
                    model_config=self.model_config,
                    prompt_messages=prompt_messages,
                    tools=sdk_tools,
                    stream=True,
                )
            except TypeError:
                # Older SDKs reject the tools kwarg on some paths.
                response = self.session.model.llm.invoke(
                    model_config=self.model_config,
                    prompt_messages=prompt_messages,
                    stream=True,
                )
        except Exception as e:
            # Let the kernel-level error policy classify rate limits/network.
            raise LLMParseError(f"llm invoke failed: {e}") from e

        # Some providers return a fully materialized message instead of a stream.
        if safe_get(response, "message") is not None:
            msg = safe_get(response, "message") or {}
            content = safe_get(msg, "content")
            text, parts = split_message_content(content)
            tcs = safe_get(msg, "tool_calls") or []
            reasoning = safe_get(msg, "reasoning_content") or ""
            from core.llm import split_think_tags

            if not reasoning:
                reasoning, text = split_think_tags(text)
            return LLMRound(
                text=text,
                reasoning=str(reasoning or ""),
                tool_calls=tuple(tcs) if isinstance(tcs, list) else (),
                nontext_parts=tuple(parts),
                chunk_count=1,
                has_unbalanced_think_tags=text.lower().count("<think>") != text.lower().count("</think>"),
            )

        record = self.usage_meter.record_chunk if self.usage_meter is not None else None
        round_ = collect_round(response, record_chunk=record, chunk_diag=self._diag)
        log.debug(
            "llm_round",
            chunks=round_.chunk_count,
            content_len=len(round_.text),
            reasoning_len=len(round_.reasoning),
            tool_calls=len(round_.tool_calls),
        )
        return round_

    def invoke_text(self, *, system: str, messages: list[Any]) -> str:
        prompt_messages = self._to_prompt_messages(system, messages)
        try:
            resp = self.session.model.llm.invoke(
                model_config=self.model_config,
                prompt_messages=prompt_messages,
                stream=False,
            )
        except TypeError:
            resp = self.session.model.llm.invoke(
                model_config=self.model_config,
                prompt_messages=prompt_messages,
            )
        if self.usage_meter is not None:
            self.usage_meter.record_chunk(resp)
        if safe_get(resp, "message") is not None:
            msg = safe_get(resp, "message") or {}
            text, _ = split_message_content(safe_get(msg, "content"))
            return str(text or "").strip()
        if isinstance(resp, str):
            return resp.strip()
        parts: list[str] = []
        for chunk in resp:
            if self.usage_meter is not None:
                self.usage_meter.record_chunk(chunk)
            delta = safe_get(chunk, "delta") or {}
            m = safe_get(delta, "message") or {}
            t, _ = split_message_content(safe_get(m, "content"))
            if t:
                parts.append(str(t))
        return "".join(parts).strip()

    # ── message translation ──────────────────────────────────────────────

    def _to_prompt_messages(self, system: str, messages: list[Any]) -> list[Any]:
        out: list[Any] = [SystemPromptMessage(content=system)] if system else []
        for m in messages:
            role = m.get("role")
            if role == "user":
                out.append(UserPromptMessage(content=m.get("content", "")))
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                sdk_calls = None
                if tool_calls:
                    import json as _json

                    sdk_calls = []
                    for tc in tool_calls:
                        raw_args = tc.get("function", {}).get("arguments") or "{}"
                        if not isinstance(raw_args, str):
                            raw_args = _json.dumps(raw_args, ensure_ascii=False)
                        sdk_calls.append(
                            AssistantPromptMessage.ToolCall(
                                id=tc.get("id") or "",
                                type="function",
                                function=AssistantPromptMessage.ToolCall.ToolCallFunction(
                                    name=tc.get("function", {}).get("name") or "",
                                    arguments=raw_args,
                                ),
                            )
                        )
                if sdk_calls is not None:
                    out.append(AssistantPromptMessage(content=m.get("content") or "", tool_calls=sdk_calls))
                else:
                    out.append(AssistantPromptMessage(content=m.get("content") or ""))
            elif role == "tool":
                out.append(
                    ToolPromptMessage(
                        tool_call_id=m.get("tool_call_id") or "",
                        name=m.get("name") or "",
                        content=m.get("content") or "",
                    )
                )
        return out

    @staticmethod
    def _to_sdk_tools(tools: list[dict[str, Any]] | None) -> list[Any] | None:
        from dify_plugin.entities.model.message import PromptMessageTool

        if not tools:
            return None
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append(
                PromptMessageTool(
                    name=fn.get("name") or "",
                    description=fn.get("description") or "",
                    parameters=fn.get("parameters") or {"type": "object", "properties": {}},
                )
            )
        return out

    @staticmethod
    def _diag(**kw: Any) -> None:
        log.chunk_diag(0, 0, **kw)
