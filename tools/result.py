from __future__ import annotations

from typing import cast

from astrbot.core.agent.tool import ToolExecResult


def tool_result(value: str) -> ToolExecResult:
    """返回普通字符串结果并保留 AstrBot ToolExecResult 类型标注。"""
    return cast(ToolExecResult, value)
