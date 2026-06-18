import json
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool, ToolExecResult

from .result import tool_result


@dataclass(config={"arbitrary_types_allowed": True})
class TencentChannelFunctionTool(FunctionTool):
    plugin: Any = None

    async def call(self, context, **kwargs) -> ToolExecResult:
        if self.plugin is None:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "腾讯频道插件未完成初始化。",
                        "hint": "请重载插件后重试。",
                    },
                    ensure_ascii=False,
                )
            )

        try:
            if self.name == "txcm_status":
                return tool_result(await self.plugin.tool_status())
            if self.name == "txcm_list_tools":
                return tool_result(await self.plugin.tool_list_tools(**kwargs))
            if self.name == "txcm_get_tool_schema":
                return tool_result(await self.plugin.tool_get_tool_schema(**kwargs))
            if self.name == "txcm_list_guilds":
                return tool_result(await self.plugin.tool_list_guilds())
            if self.name == "txcm_call_tool":
                return tool_result(await self.plugin.tool_call_tool(**kwargs))
            if self.name == "txcm_skill_guide":
                return tool_result(await self.plugin.tool_skill_guide(**kwargs))
            if self.name == "txcm_list_cli_commands":
                return tool_result(await self.plugin.tool_list_cli_commands(**kwargs))
            if self.name == "txcm_get_cli_mapping":
                return tool_result(await self.plugin.tool_get_cli_mapping(**kwargs))
            if self.name == "txcm_call_cli_command":
                return tool_result(await self.plugin.tool_call_cli_command(**kwargs))
            if self.name == "txcm_endpoint_guide":
                return tool_result(await self.plugin.tool_endpoint_guide(**kwargs))
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"未知腾讯频道工具: {self.name}",
                        "hint": "请调用 txcm_list_tools 或 txcm_list_cli_commands 查看可用工具。",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:
            logger.warning(f"[txcm] tool {self.name} failed: {exc}")
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "hint": (
                            "检查 Token、工具名和 arguments_json。写操作需在插件配置中启用 "
                            "enable_write_tools，高风险操作还需启用 enable_high_risk_tools。"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
