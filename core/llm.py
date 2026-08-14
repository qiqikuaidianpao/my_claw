"""LLM round collection with dual-channel reasoning support.

Core invariant inherited from the mini_claw 1.2.1 fix (and now enforced by
tests): one LLM call is an indivisible round. Text is never emitted to the
user mid-stream; a round containing tool calls publishes nothing, the final
round publishes complete, tag-balanced text.

Reasoning arrives over two channels, depending on platform/model age:

* Channel A (preferred): ``delta.reasoning_content`` — a dedicated field on
  the streaming delta (dify plugin SDK >= 0.9, i.e. ``LLMResultChunkDelta``).
* Channel B (fallback): reasoning wrapped inline in ``content`` as
  ``<think>...</think>`` tags by older model providers.

``collect_round`` normalizes both into a structured :class:`LLMRound`, and
``visible_text`` guarantees the user-visible answer can never be swallowed
by an unclosed thinking region.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from core.util import safe_get, split_message_content

_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)


@dataclass(frozen=True)
class LLMRound:
    """Immutable result of one complete model round."""

    text: str = ""
    reasoning: str = ""
    tool_calls: tuple[Any, ...] = ()
    nontext_parts: tuple[dict[str, Any], ...] = ()
    chunk_count: int = 0
    has_unbalanced_think_tags: bool = False

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_final(self) -> bool:
        """A round without tool calls is a publishable final round."""
        return not self.has_tool_calls


def normalize_visible_text(text: str) -> str:
    """Ensure think tags in user-visible text are balanced.

    Balanced → returned as-is (frontend renders one proper collapse plus the
    answer). Unbalanced → strip all think control tags so the answer always
    stays visible; never trap content inside an unclosed thinking region.
    """
    if not text:
        return ""
    lowered = text.lower()
    if lowered.count("<think>") == lowered.count("</think>"):
        return text
    return _THINK_TAG_RE.sub("", text).strip()


def split_think_tags(text: str) -> tuple[str, str]:
    """Split inline ``<think>...</think>`` (channel B) into (reasoning, body).

    Unclosed tags are treated as all-reasoning; the body fallback keeps any
    content after the reasoning readable. Returns ("", text) when no tags.
    """
    if not text:
        return "", ""
    m = re.search(r"<think>(.*?)</think>", text, re.DOTALL | re.IGNORECASE)
    if m:
        reasoning = m.group(1).strip()
        body = _THINK_TAG_RE.sub("", text[: m.start()] + text[m.end():]).strip()
        return reasoning, body
    if re.search(r"<think>", text, re.IGNORECASE):
        # unclosed open tag: everything after it is reasoning, before it is body
        idx = text.lower().find("<think>")
        body = text[:idx].strip()
        reasoning = _THINK_TAG_RE.sub("", text[idx:]).strip()
        return reasoning, body
    return "", text


def collect_round(
    chunks: Iterable[Any],
    record_chunk: Callable[[Any], None] | None = None,
    chunk_diag: Callable[..., None] | None = None,
) -> LLMRound:
    """Buffer one complete streaming model round (dual-channel reasoning).

    Consumes chunks, accumulating text / reasoning / tool calls / nontext
    parts without ever emitting user-visible output. Channel A reasoning
    (``delta.reasoning_content``) is preferred; inline think tags found in
    the content (channel B) are split out at the end.
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[Any] = []
    nontext: list[dict[str, Any]] = []
    count = 0
    saw_channel_a = False

    for chunk in chunks:
        if record_chunk is not None:
            try:
                record_chunk(chunk)
            except Exception:
                pass
        count += 1
        delta = safe_get(chunk, "delta") or {}
        msg = safe_get(delta, "message") or {}
        content = safe_get(msg, "content")
        t, parts = split_message_content(content)
        if parts:
            nontext.extend(parts)
        # Channel A: dedicated reasoning field (SDK >= 0.9)
        reasoning_a = safe_get(delta, "reasoning_content") or safe_get(msg, "reasoning_content")
        if isinstance(reasoning_a, str) and reasoning_a:
            saw_channel_a = True
            reasoning_parts.append(reasoning_a)
        tc = safe_get(msg, "tool_calls") or []
        if isinstance(tc, list) and tc:
            tool_calls.extend(tc)
        if t:
            text_parts.append(t)
        if chunk_diag is not None:
            chunk_diag(
                content_len=len(t or ""),
                reasoning_len=len(reasoning_a or "") if isinstance(reasoning_a, str) else 0,
                tool_calls=len(tc) if isinstance(tc, list) else 0,
            )

    raw_text = "".join(text_parts).strip()
    reasoning = "".join(reasoning_parts).strip()

    # Unbalanced check must reflect the raw stream (before any tag surgery).
    unbalanced = raw_text.lower().count("<think>") != raw_text.lower().count("</think>")

    if not saw_channel_a:
        # Channel B: reasoning embedded in content via think tags.
        reasoning_b, body_b = split_think_tags(raw_text)
        if reasoning_b:
            reasoning = reasoning_b
            raw_text = body_b

    return LLMRound(
        text=raw_text,
        reasoning=reasoning,
        tool_calls=tuple(tool_calls),
        nontext_parts=tuple(nontext),
        chunk_count=count,
        has_unbalanced_think_tags=unbalanced,
    )


def visible_text(round_: LLMRound) -> str:
    """User-visible text for a final round, with tag-balance fallback."""
    return normalize_visible_text(round_.text)
