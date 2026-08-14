"""Typed exception hierarchy for the my_claw kernel.

Replaces mini_claw's 139 bare ``except Exception`` blocks and ``{"error": str}``
dict plumbing with explicit, catchable failure modes. Boundary code (the Dify
adapter) translates these into user-visible messages; kernel code raises them.
"""
from __future__ import annotations


class MyClawError(Exception):
    """Base class for all my_claw errors. User-presentable by default."""

    user_message: str = "抱歉，处理时出现了一点问题，请稍后重试。"

    def __init__(self, detail: str = "", *, user_message: str | None = None) -> None:
        super().__init__(detail or self.__class__.__name__)
        self.detail = detail
        if user_message is not None:
            self.user_message = user_message


# ── LLM / model invocation ──────────────────────────────────────────────


class LLMError(MyClawError):
    user_message = "大模型调用失败，请稍后重试或检查模型配置。"


class LLMRateLimited(LLMError):
    user_message = "模型访问量过大（限流），请稍后重试。"


class LLMNetworkError(LLMError):
    user_message = "无法连接模型服务（网络/代理受限），请检查出站策略后重试。"


class LLMParseError(LLMError):
    """Streaming response could not be parsed at all."""

    user_message = "模型返回内容解析失败。"


# ── Tool execution ──────────────────────────────────────────────────────


class ToolError(MyClawError):
    user_message = "工具执行失败。"


class InvalidToolArguments(ToolError):
    user_message = "工具参数不合法。"


class ToolLoopDetected(ToolError):
    user_message = "检测到工具循环调用，已提前停止以避免无意义重试。"


class SkillNotEligible(ToolError):
    user_message = "该技能当前不可用（依赖缺失）。"


class SkillNotVisible(ToolError):
    user_message = "该技能当前不可由模型调用，仅当用户明确点名时可用。"


# ── Skills / packages ───────────────────────────────────────────────────


class SkillError(MyClawError):
    user_message = "技能处理失败。"


class InvalidSkillPackage(SkillError):
    user_message = "技能包格式不合法（缺少 SKILL.md 或结构错误）。"


class SkillManifestError(SkillError):
    user_message = "技能清单（SKILL.md frontmatter）校验失败。"


# ── Security / execution policy ─────────────────────────────────────────


class SecurityError(MyClawError):
    user_message = "操作被安全策略拦截。"


class ExecDenied(SecurityError):
    user_message = "该命令不允许执行（不在白名单或违反执行策略）。"


class PathEscapeDenied(SecurityError):
    user_message = "路径越界，已拦截。"


class FetchDenied(SecurityError):
    user_message = "该网址不允许抓取（SSRF 防护拦截）。"


# ── Storage / workspace ─────────────────────────────────────────────────


class StorageError(MyClawError):
    user_message = "存储读写失败。"


class WorkspaceError(MyClawError):
    user_message = "工作区操作失败。"


class ContextBudgetExceeded(MyClawError):
    user_message = "上下文预算不足，请压缩对话或开启新会话。"
