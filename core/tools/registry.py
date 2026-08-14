"""Tool handler registry — replaces mini_claw's 620-line if/elif dispatch.

Each agent tool declares itself once with the :func:`tool` decorator:
schema, validator, progress message and execution live together, and the
kernel loop only orchestrates (validate → execute → feed result back into
the conversation). Adding a tool no longer touches four places.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.errors import InvalidToolArguments


@dataclass(frozen=True)
class ToolResult:
    """Normalized outcome fed back into the agent conversation."""

    content: str  # JSON string shown to the model as the tool observation
    display: str = ""  # optional progress line emitted to the user
    ok: bool = True


ProgressFn = Callable[[str], None]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    handler: Callable[..., ToolResult]
    progress: str = ""  # user-visible progress line emitted before execution
    required: tuple[str, ...] = ()
    visible_to_model: bool = True


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    description: str,
    parameters: dict[str, Any],
    required: tuple[str, ...] = (),
    progress: str = "",
    visible_to_model: bool = True,
) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
    def deco(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        if name in _REGISTRY:
            raise ValueError(f"duplicate tool name: {name}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            required=required,
            progress=progress,
            visible_to_model=visible_to_model,
        )
        return fn

    return deco


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_specs() -> list[ToolSpec]:
    return [spec for spec in _REGISTRY.values() if spec.visible_to_model]


def clear() -> None:
    """Test helper — drop all registrations."""
    _REGISTRY.clear()


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    missing = [k for k in spec.required if k not in args or args[k] in (None, "")]
    if missing:
        raise InvalidToolArguments(
            f"tool={spec.name} missing required: {','.join(missing)}"
        )
    return args
