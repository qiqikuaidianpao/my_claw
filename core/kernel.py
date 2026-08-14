"""AgentKernel — the explicit four-phase state machine of my_claw.

Phases (each delegating to an injected component, no god method):

1. ``preflight``  — approval gate + persona bootstrap (M2 components).
2. ``agent loop`` — whole-round LLM calls; intermediate rounds execute tools
   via the registry and publish nothing; the final round publishes complete
   tag-balanced text (the 1.2.1 invariant, now structural).
3. ``delivery``   — final text + registered artifacts via MessageEmitter.

The kernel talks only to ports (LLMClient / MessageEmitter) and pure core
types, so it runs in unit tests with fakes.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from core import log
from core.errors import MyClawError, ToolLoopDetected
from core.llm import LLMRound, visible_text
from core.ports import LLMClient, MessageEmitter
from core.session import SessionContext
from core.tools import registry as tools_reg


class AgentKernel:
    def __init__(
        self,
        *,
        llm: LLMClient,
        emitter: MessageEmitter,
        compactor: Callable[[SessionContext], None] | None = None,
        tool_executor: Callable[..., "tools_reg.ToolResult"] | None = None,
    ) -> None:
        self.llm = llm
        self.emitter = emitter
        self.compactor = compactor
        # tool_executor(name, args, ctx, emit) -> ToolResult; defaults to registry dispatch
        self.tool_executor = tool_executor or self._default_tool_executor

    # ── main entry ───────────────────────────────────────────────────────

    def run(self, ctx: SessionContext) -> SessionContext:
        """Run the agent loop to completion. Raises MyClawError subclasses."""
        for _ in self.run_iter(ctx):
            pass
        return ctx

    def run_iter(self, ctx: SessionContext):
        """Generator variant of run(): yields after each emission batch so a
        host generator can drain the emitter between steps (live streaming)."""
        for step in range(ctx.max_tool_turns):
            if ctx.timed_out:
                ctx.final_text = f"⏱ 已超过超时时间（{ctx.timeout_seconds}s），提前停止。"
                break
            if self.compactor is not None:
                self.compactor(ctx)

            log.debug("kernel_step", step=step + 1, messages=len(ctx.messages), elapsed=round(ctx.elapsed, 1))

            round_ = self.llm.invoke_round(system=ctx.system_prompt, messages=ctx.messages, tools=self._tool_schemas())
            ctx.rounds.append(round_)

            if round_.has_tool_calls:
                self._execute_tools(round_, ctx)
                yield
                if ctx.final_text:  # forced stop (loop breaker etc.)
                    break
                if step >= ctx.max_tool_turns - 1:
                    if ctx.artifacts:
                        ctx.final_text = "已生成文件。"
                    break
                continue

            # final round
            if not round_.text and not round_.nontext_parts:
                ctx.empty_responses += 1
                if ctx.empty_responses < 3:
                    self._append_user_note(ctx, "你刚才没有输出任何内容。请继续完成任务：需要工具就发起调用，否则直接给出最终答复。")
                    continue
                ctx.final_text = "模型连续返回空响应，未生成任何结果。"
                break

            ctx.final_text = round_.text
            ctx.messages.append({"role": "assistant", "content": round_.text})
            self._emit_final(ctx, round_)
            yield
            break
        else:
            ctx.final_text = ctx.final_text or (
                f"❌已达最大执行轮数（{ctx.max_tool_turns}）。可尝试提高超时或检查是否陷入重复调用。"
            )
            if not ctx.final_text_emitted:
                self._emit_text(ctx.final_text)
                ctx.final_text_emitted = True
            yield

    # ── tool dispatch ────────────────────────────────────────────────────

    def _execute_tools(self, round_: LLMRound, ctx: SessionContext) -> None:
        from core.util import shorten_text

        # The assistant's tool-call message must precede tool results in the
        # conversation history, or real providers reject the orphan tool msg.
        ctx.messages.append(
            {"role": "assistant", "content": round_.text or "", "tool_calls": [self._tool_call_plain(tc) for tc in round_.tool_calls]}
        )

        for tc in round_.tool_calls:
            call_id = self._tool_call_id(tc)
            name = self._tool_call_name(tc)
            args = self._tool_call_args(tc)
            sig = f"{name}|{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
            log.debug("tool_call", tool=name, args=shorten_text(args, 300))

            spec = tools_reg.get(name)
            if spec is None:
                self._feed_tool_result(ctx, call_id, name, {"error": "unknown_tool", "tool": name})
                continue
            if spec.progress:
                try:
                    self.emitter.text(spec.progress.format(**args) if args else spec.progress)
                except (KeyError, IndexError, ValueError):
                    import re as _re

                    self.emitter.text(_re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "", spec.progress))

            try:
                result = self.tool_executor(spec, args, ctx, self.emitter)
                digest = shorten_text(result.content, 120)
                warned, stop = ctx.loop_guard.observe(sig, digest)
                if stop:
                    ctx.final_text = "❌检测到工具循环调用，已熔断停止。请调整策略或参数。"
                    self._feed_tool_result(ctx, call_id, name, {"error": "tool_loop_stopped"})
                    break
                if warned:
                    self._append_user_note(ctx, f"你正在重复调用 `{name}`，请改变策略，避免死循环。")
                self._feed_tool_result(ctx, call_id, name, json.loads(result.content) if result.content.startswith("{") else {"result": result.content})
            except MyClawError as e:
                log.warning("tool_failed", tool=name, detail=e.detail)
                self._feed_tool_result(ctx, call_id, name, {"error": e.__class__.__name__, "detail": e.detail})
            except Exception as e:  # boundary: unexpected tool failure must not kill the session
                log.error("tool_crashed", tool=name, detail=str(e))
                self._feed_tool_result(ctx, call_id, name, {"error": "tool_crashed", "detail": str(e)})

    def _default_tool_executor(self, spec: "tools_reg.ToolSpec", args: dict[str, Any], ctx: SessionContext, emitter: MessageEmitter) -> "tools_reg.ToolResult":
        args = tools_reg.validate_arguments(spec, args)
        return spec.handler(ctx=ctx, emitter=emitter, **args)

    # ── helpers ──────────────────────────────────────────────────────────

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": s.name, "description": s.description, "parameters": s.parameters}}
            for s in tools_reg.all_specs()
        ]

    def _emit_final(self, ctx: SessionContext, round_: LLMRound) -> None:
        text = visible_text(round_)
        if text:
            self._emit_text(text)
        ctx.final_text_emitted = True

    def _emit_text(self, text: str) -> None:
        step = 6  # typewriter pacing for readable streaming after buffering
        for i in range(0, len(text), step):
            self.emitter.text(text[i : i + step])

    def _feed_tool_result(self, ctx: SessionContext, call_id: str, name: str, payload: dict[str, Any]) -> None:
        """Append the tool observation into the conversation history."""
        ctx.messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }
        )

    def _append_user_note(self, ctx: SessionContext, note: str) -> None:
        ctx.messages.append({"role": "user", "content": note})

    @staticmethod
    def _tool_call_plain(tc: Any) -> dict[str, Any]:
        """Normalize an SDK tool call into a plain dict for history storage.

        arguments is canonicalized to a JSON string — SDK ToolCallFunction
        rejects dicts when history is re-serialized for the next round.
        """
        import json as _json

        args = AgentKernel._tool_call_args(tc)
        if not isinstance(args, str):
            args = _json.dumps(args, ensure_ascii=False)
        return {"id": AgentKernel._tool_call_id(tc), "function": {"name": AgentKernel._tool_call_name(tc), "arguments": args}}

    @staticmethod
    def _tool_call_id(tc: Any) -> str:
        from core.util import safe_get

        return str(safe_get(tc, "id") or "")

    @staticmethod
    def _tool_call_name(tc: Any) -> str:
        from core.util import safe_get

        fn = safe_get(tc, "function") or {}
        return str(safe_get(fn, "name") or "")

    @staticmethod
    def _tool_call_args(tc: Any) -> dict[str, Any]:
        import json as _json

        from core.util import safe_get

        fn = safe_get(tc, "function") or {}
        raw = safe_get(fn, "arguments") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            parsed = _json.loads(raw) if isinstance(raw, str) else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


__all__ = ["AgentKernel", "ToolLoopDetected"]
