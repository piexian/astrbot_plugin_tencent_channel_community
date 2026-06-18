from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig, sp
from astrbot.core.star.filter.command import GreedyStr

from .tools import TencentChannelFunctionTool
from .tools.schema import object_parameters, string_param

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except ImportError:
    get_astrbot_plugin_data_path = None


PLUGIN_NAME = "astrbot_plugin_tencent_channel_community"
PLUGIN_VERSION = "v0.2.0"
DEFAULT_MCP_ENDPOINT = "https://graph.qq.com/mcp_gateway/open_platform_agent_mcp/mcp"
DEFAULT_AUTH_BASE_URL = (
    "https://connect.qq.com/http2rpc/gotrpc/noauth/"
    "trpc.group_pro.open_developer_console.OpenDeveloperConsoleV2Trpc"
)
DEFAULT_DEVICE_CODE_REQUEST_URL = f"{DEFAULT_AUTH_BASE_URL}/RequestDeviceCode"
DEFAULT_DEVICE_TOKEN_POLL_URL = f"{DEFAULT_AUTH_BASE_URL}/PollDeviceToken"
MCP_PROTOCOL_VERSION = "2024-11-05"
REQUEST_DEVICE_CODE_OIDB = {"uint32_command": "0x995b", "uint32_service_type": "1"}
POLL_DEVICE_TOKEN_OIDB = {"uint32_command": "0x995d", "uint32_service_type": "1"}
TXCM_LLM_TOOL_NAMES = (
    "txcm_status",
    "txcm_list_tools",
    "txcm_get_tool_schema",
    "txcm_list_guilds",
    "txcm_call_tool",
    "txcm_skill_guide",
    "txcm_list_cli_commands",
    "txcm_get_cli_mapping",
    "txcm_call_cli_command",
    "txcm_endpoint_guide",
)

CONFIG_PATHS = {
    "qq_ai_connect_token": ("account_settings", "qq_ai_connect_token"),
    "mcp_endpoint": ("connection_settings", "mcp_endpoint"),
    "request_timeout_seconds": ("connection_settings", "request_timeout_seconds"),
    "proxy": ("connection_settings", "proxy"),
    "enable_write_tools": ("tool_settings", "enable_write_tools"),
    "enable_high_risk_tools": ("tool_settings", "enable_high_risk_tools"),
    "cache_tool_schema": ("tool_settings", "cache_tool_schema"),
    "device_code_request_url": ("login_settings", "device_code_request_url"),
    "device_token_poll_url": ("login_settings", "device_token_poll_url"),
    "login_timeout_seconds": ("login_settings", "login_timeout_seconds"),
    "login_poll_interval_seconds": ("login_settings", "login_poll_interval_seconds"),
    "login_request_payload_json": ("login_settings", "login_request_payload_json"),
    "login_poll_payload_json": ("login_settings", "login_poll_payload_json"),
}

CONFIG_DEFAULTS = {
    "qq_ai_connect_token": "",
    "mcp_endpoint": DEFAULT_MCP_ENDPOINT,
    "request_timeout_seconds": 30,
    "proxy": "",
    "enable_write_tools": False,
    "enable_high_risk_tools": False,
    "cache_tool_schema": True,
    "device_code_request_url": DEFAULT_DEVICE_CODE_REQUEST_URL,
    "device_token_poll_url": DEFAULT_DEVICE_TOKEN_POLL_URL,
    "login_timeout_seconds": 420,
    "login_poll_interval_seconds": 3,
    "login_request_payload_json": "{}",
    "login_poll_payload_json": "{}",
}

HIGH_RISK_TOOLS = {
    "del_feed",
    "delete_channel",
    "kick_guild_member",
    "leave_guild",
    "modify_member_shut_up",
    "do_comment",
    "do_reply",
    "deal_notice",
}

WRITE_TOOLS = {
    "alter_feed",
    "apply_media_upload",
    "batch_essence",
    "change_role_member",
    "create_channel",
    "create_guild",
    "create_guild_role_group",
    "deal_notice",
    "del_feed",
    "delete_channel",
    "do_comment",
    "do_feed_prefer",
    "do_like",
    "do_reply",
    "join_guild",
    "kick_guild_member",
    "leave_guild",
    "modify_channel",
    "modify_guild_number",
    "modify_guild_role_group",
    "modify_member_shut_up",
    "move_feed",
    "publish_feed",
    "push_essence_feed",
    "push_group_normal_dm_msg",
    "push_qq_msg",
    "report_user_guild_read_digest",
    "top_feed_action",
    "update_guild_info",
    "update_join_guild_setting",
    "upload_guild_avatar",
    "upload_guild_avatar_pre",
}

DEFAULT_GUILD_LIST_ARGUMENTS = {
    "bytesCookie": "",
    "filter": {
        "filter": {
            "uint32MemberNum": 1,
            "uint32GuildName": 1,
            "uint32Profile": 1,
            "uint32FaceSeq": 1,
            "uint32GuildNumber": 1,
            "uint32CreateTime": 1,
        },
        "userFilter": {"uint32Role": 1},
    },
}

CLI_COMMANDS: dict[str, dict[str, Any]] = {
    "feed.get-guild-feeds": {
        "tool": "get_guild_feeds",
        "group": "read",
        "risk": "read",
        "description": "获取腾讯频道主页帖子",
    },
    "feed.get-channel-timeline-feeds": {
        "tool": "get_channel_timeline_feeds",
        "group": "read",
        "risk": "read",
        "description": "获取版块帖子列表",
    },
    "feed.get-feed-detail": {
        "tool": "get_feed_detail",
        "group": "read",
        "risk": "read",
        "description": "查看帖子详情",
    },
    "feed.get-feed-comments": {
        "tool": "get_feed_comments",
        "group": "read",
        "risk": "read",
        "description": "查看帖子评论",
    },
    "feed.search-guild-feeds": {
        "tool": "get_search_guild_feed",
        "group": "read",
        "risk": "read",
        "description": "搜索频道内帖子",
    },
    "feed.get-feed-share-url": {
        "tool": "get_share_url",
        "group": "read",
        "risk": "read",
        "description": "获取帖子分享短链",
        "note": "CLI 会本地编码 businessParam；插件只暴露对应 MCP tool。",
    },
    "feed.get-notices": {
        "tool": "get_interact_notice",
        "group": "read",
        "risk": "read",
        "description": "查看互动消息",
    },
    "feed.get-next-page-replies": {
        "tool": "get_next_page_replies",
        "group": "read",
        "risk": "read",
        "description": "查看更多评论回复",
    },
    "feed.publish-feed": {
        "tool": "publish_feed",
        "group": "write",
        "risk": "write",
        "description": "发表帖子",
    },
    "feed.del-feed": {
        "tool": "del_feed",
        "group": "write",
        "risk": "high-risk-write",
        "description": "删除帖子",
    },
    "feed.do-comment": {
        "tool": "do_comment",
        "group": "write",
        "risk": "write",
        "description": "发表或删除评论",
    },
    "feed.do-reply": {
        "tool": "do_reply",
        "group": "write",
        "risk": "write",
        "description": "发表或删除回复",
    },
    "feed.do-like": {
        "tool": "do_like",
        "group": "write",
        "risk": "write",
        "description": "评论或回复点赞",
    },
    "feed.do-feed-prefer": {
        "tool": "do_feed_prefer",
        "group": "write",
        "risk": "write",
        "description": "帖子点赞或取消",
    },
    "feed.alter-feed": {
        "tool": "alter_feed",
        "group": "write",
        "risk": "write",
        "description": "编辑帖子",
    },
    "feed.top-feed": {
        "tool": "top_feed_action",
        "group": "write",
        "risk": "write",
        "description": "帖子置顶或取消置顶",
    },
    "feed.set-feed-essence": {
        "tool": "batch_essence",
        "group": "write",
        "risk": "write",
        "description": "设置或取消精华",
    },
    "feed.push-essence-feed": {
        "tool": "push_essence_feed",
        "group": "write",
        "risk": "write",
        "description": "推送精华帖通知",
    },
    "feed.move-feed": {
        "tool": "move_feed",
        "group": "write",
        "risk": "write",
        "description": "移动帖子到其他版块",
    },
    "feed.quick-publish": {
        "tool": "",
        "group": "shortcut",
        "risk": "write",
        "description": "选择频道和版块后一键发帖",
        "note": "CLI 本地多步流程；插件侧请用列表工具选择目标后调用 publish_feed。",
    },
    "feed.search-and-comment": {
        "tool": "",
        "group": "shortcut",
        "risk": "write",
        "description": "搜索帖子并评论",
        "note": "CLI 本地多步流程；插件侧请组合 get_search_guild_feed 与 do_comment。",
    },
    "feed.delete-and-mute": {
        "tool": "",
        "group": "shortcut",
        "risk": "high-risk-write",
        "description": "搜帖删帖并禁言",
        "note": "CLI 本地高风险流程；插件侧请显式确认后组合 del_feed 与 modify_member_shut_up。",
    },
    "feed.latest-feeds-detail": {
        "tool": "",
        "group": "shortcut",
        "risk": "read",
        "description": "获取最新帖子详情",
        "note": "CLI 本地多步流程；插件侧请组合 get_guild_feeds 与 get_feed_detail。",
    },
    "feed.hot-feeds-detail": {
        "tool": "",
        "group": "shortcut",
        "risk": "read",
        "description": "获取热门帖子详情",
        "note": "CLI 本地多步流程；插件侧请组合 get_guild_feeds 与 get_feed_detail。",
    },
    "manage.get-guild-info": {
        "tool": "get_guild_info",
        "group": "query",
        "risk": "read",
        "description": "查看腾讯频道资料",
    },
    "manage.get-my-join-guild-info": {
        "tool": "get_my_join_guild_info",
        "group": "query",
        "risk": "read",
        "description": "查看我的腾讯频道列表",
    },
    "manage.get-user-info": {
        "tool": "get_user_info",
        "group": "query",
        "risk": "read",
        "description": "查看用户资料",
    },
    "manage.get-guild-member-list": {
        "tool": "get_guild_member_list",
        "group": "query",
        "risk": "read",
        "description": "查看成员列表",
    },
    "manage.guild-member-search": {
        "tool": "guild_member_search",
        "group": "query",
        "risk": "read",
        "description": "按昵称搜索成员",
    },
    "manage.get-guild-channel-list": {
        "tool": "get_guild_channel_list",
        "group": "query",
        "risk": "read",
        "description": "查看版块列表",
    },
    "manage.search-guild-content": {
        "tool": "search_guild_content",
        "group": "query",
        "risk": "read",
        "description": "搜索腾讯频道、帖子或作者",
    },
    "manage.get-join-guild-setting": {
        "tool": "get_join_guild_setting",
        "group": "query",
        "risk": "read",
        "description": "查看腾讯频道加入设置",
    },
    "manage.get-guild-share-url": {
        "tool": "get_share_url",
        "group": "query",
        "risk": "read",
        "description": "获取腾讯频道分享短链",
    },
    "manage.get-share-info": {
        "tool": "get_share_info",
        "group": "query",
        "risk": "read",
        "description": "解析 pd.qq.com 分享链接",
    },
    "manage.kick-guild-member": {
        "tool": "kick_guild_member",
        "group": "write",
        "risk": "high-risk-write",
        "description": "踢出成员",
    },
    "manage.modify-member-shut-up": {
        "tool": "modify_member_shut_up",
        "group": "write",
        "risk": "write",
        "description": "禁言或解禁成员",
    },
    "manage.update-guild-info": {
        "tool": "update_guild_info",
        "group": "write",
        "risk": "write",
        "description": "修改腾讯频道名称或简介",
    },
    "manage.modify-guild-number": {
        "tool": "modify_guild_number",
        "group": "write",
        "risk": "write",
        "description": "修改腾讯频道号",
    },
    "manage.create-guild-role-group": {
        "tool": "create_guild_role_group",
        "group": "write",
        "risk": "write",
        "description": "创建身份组",
    },
    "manage.modify-guild-role-group": {
        "tool": "modify_guild_role_group",
        "group": "write",
        "risk": "write",
        "description": "修改身份组",
    },
    "manage.add-role-members": {
        "tool": "change_role_member",
        "group": "write",
        "risk": "write",
        "description": "向身份组添加成员",
    },
    "manage.remove-role-members": {
        "tool": "change_role_member",
        "group": "write",
        "risk": "high-risk-write",
        "description": "从身份组移除成员",
    },
    "manage.join-guild": {
        "tool": "join_guild",
        "group": "write",
        "risk": "write",
        "description": "加入腾讯频道",
    },
    "manage.create-channel": {
        "tool": "create_channel",
        "group": "write",
        "risk": "write",
        "description": "创建子版块",
    },
    "manage.delete-channel": {
        "tool": "delete_channel",
        "group": "write",
        "risk": "high-risk-write",
        "description": "删除版块",
    },
    "manage.modify-channel": {
        "tool": "modify_channel",
        "group": "write",
        "risk": "write",
        "description": "修改版块名称",
    },
    "manage.upload-guild-avatar": {
        "tool": "upload_guild_avatar",
        "group": "write",
        "risk": "write",
        "description": "修改腾讯频道头像",
    },
    "manage.create-theme-private-guild": {
        "tool": "create_guild",
        "group": "write",
        "risk": "write",
        "description": "创建公开或私密频道",
    },
    "manage.add-admin": {
        "tool": "change_role_member",
        "group": "write",
        "risk": "write",
        "description": "设置超级管理员",
        "note": "CLI 使用 change_role_member 并写死超级管理员 roleId=2。",
    },
    "manage.remove-admin": {
        "tool": "change_role_member",
        "group": "write",
        "risk": "high-risk-write",
        "description": "移除超级管理员",
        "note": "CLI 使用 change_role_member 并写死超级管理员 roleId=2。",
    },
    "manage.push-group-dm-msg": {
        "tool": "push_group_normal_dm_msg",
        "group": "write",
        "risk": "write",
        "description": "发送频道私信",
    },
    "manage.update-join-guild-setting": {
        "tool": "update_join_guild_setting",
        "group": "write",
        "risk": "write",
        "description": "修改腾讯频道加入设置",
    },
    "manage.leave-guild": {
        "tool": "leave_guild",
        "group": "write",
        "risk": "high-risk-write",
        "description": "退出腾讯频道",
    },
    "manage.notices-on": {
        "tool": "",
        "group": "write",
        "risk": "write",
        "description": "开启频道消息通知",
        "note": "CLI 本地订阅/OpenClaw 推送流程；AstrBot 插件不启动 CLI daemon。",
    },
    "manage.notices-off": {
        "tool": "",
        "group": "write",
        "risk": "write",
        "description": "关闭频道消息通知",
        "note": "CLI 本地订阅/OpenClaw 推送流程；AstrBot 插件不启动 CLI daemon。",
    },
    "manage.notices-status": {
        "tool": "",
        "group": "query",
        "risk": "read",
        "description": "查看频道消息通知状态",
        "note": "CLI 读取本地 ~/.qqcli/subscription 状态。",
    },
    "manage.check-notices": {
        "tool": "",
        "group": "query",
        "risk": "read",
        "description": "增量检查频道通知",
        "note": "CLI 本地流程会组合 query_user_guild_digest、get_interact_notice、get_notice_list 和 query_normal_dm_list。",
    },
    "manage.subscribe-notices": {
        "tool": "",
        "group": "write",
        "risk": "write",
        "description": "开启频道消息通知",
        "note": "notices-on 的兼容别名。",
    },
    "manage.unsubscribe-notices": {
        "tool": "",
        "group": "write",
        "risk": "write",
        "description": "关闭频道消息通知",
        "note": "notices-off 的兼容别名。",
    },
    "manage.check-new-notices": {
        "tool": "",
        "group": "query",
        "risk": "read",
        "description": "检查新的频道通知",
        "note": "check-notices 的兼容别名。",
    },
    "manage.get-recent-notices": {
        "tool": "",
        "group": "query",
        "risk": "read",
        "description": "获取最近的通知记录",
        "note": "CLI 读取本地通知记录。",
    },
    "manage.deal-notice": {
        "tool": "deal_notice",
        "group": "write",
        "risk": "write",
        "description": "处理系统通知",
    },
    "manage.notify-daemon": {
        "tool": "",
        "group": "write",
        "risk": "write",
        "description": "启动后台通知检查服务",
        "note": "CLI 本地 daemon；AstrBot 插件不启动外部进程。",
    },
    "manage.search-and-join": {
        "tool": "",
        "group": "shortcut",
        "risk": "write",
        "description": "搜索频道并加入",
        "note": "CLI 本地多步流程；插件侧请组合 search_guild_content 与 join_guild。",
    },
}

ENDPOINT_GUIDE: dict[str, dict[str, Any]] = {
    "login_request_device_code": {
        "method": "POST",
        "url": DEFAULT_DEVICE_CODE_REQUEST_URL,
        "headers": {"X-Oidb": REQUEST_DEVICE_CODE_OIDB},
        "body": {"device_id": "<uuid>"},
        "description": "申请扫码/授权链接设备码。",
    },
    "login_poll_device_token": {
        "method": "POST",
        "url": DEFAULT_DEVICE_TOKEN_POLL_URL,
        "headers": {"X-Oidb": POLL_DEVICE_TOKEN_OIDB},
        "body": {"device_id": "<uuid>", "device_code": "<device_code>"},
        "description": "轮询设备授权结果，成功时返回 Token。",
    },
    "mcp_json_rpc": {
        "method": "POST",
        "url": DEFAULT_MCP_ENDPOINT,
        "headers": {
            "Authorization": "Bearer <token>",
            "Content-Type": "application/json",
            "X-Forwarded-Method": "POST",
        },
        "body": {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "<tool_name>", "arguments": {}},
        },
        "description": "所有 feed/manage 原子业务能力共用的 MCP JSON-RPC 端点。",
    },
    "media_sliceupload": {
        "method": "POST",
        "url": "http://<upload_host>:<upload_port>/sliceupload",
        "description": (
            "发帖/改帖上传图片或视频时由 apply_media_upload 返回动态上传地址，"
            "请求体是 CLI 内部编码的分片上传二进制协议。"
        ),
    },
    "skill_update_check": {
        "method": "HEAD",
        "url": "https://connect.qq.com/skills/tencent-channel-community.zip",
        "description": "官方 Skill/CLI 更新检测端点，读取 x-cos-meta-tcc-version 等响应头。",
    },
}


class TencentChannelError(Exception):
    """腾讯频道接口错误。

    Args:
        message: 可展示给管理员的错误信息。
        data: 上游返回的原始数据。
    """

    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.data = data


def _json_dumps(data: Any, limit: int = 4000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... 已截断 ..."


def _normalize_tool_name(name: str) -> str:
    return str(name or "").strip().replace("-", "_")


def _parse_json_text(text: str, *, fallback: Any = None) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


@register(
    PLUGIN_NAME,
    "腾讯频道社区管理工具",
    "通过 tencent-channel-cli 接口管理 QQ 频道，内置使用规则并注册 LLM Tools。",
    PLUGIN_VERSION,
)
class TencentChannelCommunityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._server_info: dict[str, Any] | None = None
        self._tool_cache: list[dict[str, Any]] | None = None
        self._login_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        self.context.add_llm_tools(
            TencentChannelFunctionTool(
                name="txcm_status",
                description="检查 tencent-channel-cli 接口、Token 配置和基础连通性。",
                parameters=object_parameters({}),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_list_tools",
                description="列出腾讯频道 MCP 可用工具，可按关键词过滤。",
                parameters=object_parameters(
                    {"query": string_param("可选。按工具名或描述过滤。")}
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_get_tool_schema",
                description="获取某个腾讯频道 MCP 工具的 JSON Schema。",
                parameters=object_parameters(
                    {"tool_name": string_param("工具名，支持下划线或连字符。")},
                    required=["tool_name"],
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_list_guilds",
                description="获取当前账号已加入的腾讯频道列表。",
                parameters=object_parameters({}),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_call_tool",
                description=(
                    "调用腾讯频道 MCP 原始工具。arguments_json 必须是 JSON object 字符串。"
                    "写操作和高风险操作受插件配置开关限制。"
                ),
                parameters=object_parameters(
                    {
                        "tool_name": string_param("MCP 工具名，支持下划线或连字符。"),
                        "arguments_json": string_param(
                            '传给 MCP tools/call 的 arguments JSON object 字符串，例如 {"guildId":"123"}。'
                        ),
                    },
                    required=["tool_name", "arguments_json"],
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_skill_guide",
                description="读取内置腾讯频道 Skill 使用规则，帮助模型选择工具和控制风险。",
                parameters=object_parameters(
                    {
                        "topic": string_param(
                            "可选。guild/member/feed/notification/risk/login/cli/endpoint/media/shortcut。"
                        )
                    }
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_list_cli_commands",
                description="列出 tencent-channel-cli 官方命令与当前插件 MCP 对齐关系。",
                parameters=object_parameters(
                    {"query": string_param("可选。按命令名、MCP 工具名或说明过滤。")}
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_get_cli_mapping",
                description="查询某个 tencent-channel-cli 命令对应的 MCP tool 或本地流程说明。",
                parameters=object_parameters(
                    {
                        "command": string_param(
                            "CLI 命令，例如 feed.publish-feed 或 manage get-guild-info。"
                        )
                    },
                    required=["command"],
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_call_cli_command",
                description=(
                    "按 CLI 命令名定位 MCP tool 并调用。arguments_json 必须是对应 MCP tool schema 的 "
                    "JSON object 字符串，写操作和高风险操作受插件配置开关限制。"
                ),
                parameters=object_parameters(
                    {
                        "command": string_param("CLI 命令，例如 feed.publish-feed。"),
                        "arguments_json": string_param(
                            '对应 MCP tool 的 arguments JSON object 字符串，例如 {"guildId":"123"}。'
                        ),
                    },
                    required=["command", "arguments_json"],
                ),
                plugin=self,
            ),
            TencentChannelFunctionTool(
                name="txcm_endpoint_guide",
                description="查看腾讯频道 CLI/Skill 使用到的接口参考。",
                parameters=object_parameters(
                    {
                        "topic": string_param(
                            "可选。login_request_device_code/login_poll_device_token/mcp_json_rpc/media_sliceupload/skill_update_check。"
                        )
                    }
                ),
                plugin=self,
            ),
        )
        self._ensure_default_tool_permissions()
        logger.info(f"[{PLUGIN_NAME}] Tencent Channel LLM tools registered")

    def _ensure_default_tool_permissions(self) -> None:
        """把本插件 LLM Tools 的默认权限交给 AstrBot 权限配置。"""
        try:
            perms_store = sp.get(
                "tool_permissions",
                {},
                scope="global",
                scope_id="global",
            )
            if not isinstance(perms_store, dict):
                perms_store = {}
            defaults = perms_store.get("_default", {})
            if not isinstance(defaults, dict):
                defaults = {}

            changed = False
            for tool_name in TXCM_LLM_TOOL_NAMES:
                if tool_name not in defaults:
                    defaults[tool_name] = "admin"
                    changed = True
            if changed:
                perms_store["_default"] = defaults
                sp.put(
                    "tool_permissions",
                    perms_store,
                    scope="global",
                    scope_id="global",
                )
        except Exception as exc:
            logger.warning(
                f"[{PLUGIN_NAME}] failed to set default tool permissions: {exc}"
            )

    async def terminate(self) -> None:
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        if self._session:
            await self._session.close()
            self._session = None

    def _cfg(self, key: str, default: Any = None) -> Any:
        path = CONFIG_PATHS.get(key)
        fallback = CONFIG_DEFAULTS.get(key, default)
        if path:
            section = self.config.get(path[0], {})
            if isinstance(section, dict) and path[1] in section:
                return section[path[1]]
        return self.config.get(key, fallback)

    def _set_cfg(self, key: str, value: Any) -> None:
        path = CONFIG_PATHS.get(key)
        if path:
            section = self.config.get(path[0])
            if not isinstance(section, dict):
                section = {}
                self.config[path[0]] = section
            section[path[1]] = value
            return
        self.config[key] = value

    def _save_config(self) -> None:
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()

    def _plugin_data_dir(self) -> Path:
        if get_astrbot_plugin_data_path is not None:
            root = Path(get_astrbot_plugin_data_path())
        else:
            root = Path(__file__).resolve().parent / "data" / "plugin_data"
        path = root / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(trust_env=True)
        return self._session

    def _timeout(self) -> aiohttp.ClientTimeout:
        try:
            seconds = int(self._cfg("request_timeout_seconds", 30))
        except (TypeError, ValueError):
            seconds = 30
        return aiohttp.ClientTimeout(total=max(5, min(seconds, 180)))

    def _token(self) -> str:
        token = str(self._cfg("qq_ai_connect_token", "") or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    async def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        session = await self._get_session()
        proxy = str(self._cfg("proxy", "") or "").strip() or None
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                proxy=proxy,
                timeout=self._timeout(),
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    if response.status in {401, 403}:
                        message = "腾讯频道鉴权失败，请检查 QQ AI Connect Token 或重新 /txcm login。"
                    elif response.status == 429:
                        message = "腾讯频道接口触发频率限制，请稍后再试。"
                    else:
                        message = f"腾讯频道端点返回 HTTP {response.status}。"
                    raise TencentChannelError(
                        f"{message} 响应片段：{text[:300]}",
                    )
        except TimeoutError as exc:
            raise TencentChannelError("请求腾讯频道端点超时。") from exc
        except aiohttp.ClientError as exc:
            raise TencentChannelError(f"请求腾讯频道端点失败: {exc}") from exc

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TencentChannelError("腾讯频道端点返回了非 JSON 响应。") from exc
        return data

    async def _mcp_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        token_required: bool = False,
    ) -> dict[str, Any]:
        token = self._token()
        if token_required and not token:
            raise TencentChannelError(
                "未配置 QQ AI Connect Token。请使用 /txcm token 写入。"
            )

        headers = {"Content-Type": "application/json", "X-Forwarded-Method": "POST"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "jsonrpc": "2.0",
            "id": f"astrbot-{int(time.time() * 1000)}",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        data = await self._post_json(str(self._cfg("mcp_endpoint")), payload, headers)
        if "error" in data:
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            if "token" in str(message).lower() or "auth" in str(message).lower():
                hint = "请检查 Token，或使用 /txcm login 重新授权。"
            else:
                hint = "请检查请求参数和 MCP 接口配置。"
            raise TencentChannelError(f"MCP {method} 失败: {message}。{hint}", data)
        return data

    async def _initialize_mcp(self) -> dict[str, Any]:
        if self._server_info:
            return self._server_info

        response = await self._mcp_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
            },
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise TencentChannelError("MCP initialize 返回格式异常。", response)
        self._server_info = result
        return result

    async def _list_mcp_tools(self, *, force: bool = False) -> list[dict[str, Any]]:
        if (
            self._tool_cache is not None
            and not force
            and self._cfg("cache_tool_schema")
        ):
            return self._tool_cache

        await self._initialize_mcp()
        response = await self._mcp_request("tools/list")
        tools = response.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise TencentChannelError("MCP tools/list 返回格式异常。", response)
        self._tool_cache = [tool for tool in tools if isinstance(tool, dict)]
        return self._tool_cache

    def _extract_tool_result(self, response: dict[str, Any]) -> dict[str, Any]:
        result = response.get("result")
        if not isinstance(result, dict):
            raise TencentChannelError("MCP tools/call 返回格式异常。", response)

        parsed: dict[str, Any] = {
            "is_error": bool(result.get("isError")),
            "code": None,
            "message": None,
            "content": [],
            "raw": result,
        }
        for item in result.get("content", []):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = str(item.get("text") or "")
            parsed["content"].append(text)
            lower = text.lower()
            if lower.startswith("code(") and ":" in text:
                code_text = text.split(":", 1)[1].strip()
                parsed["code"] = int(code_text) if code_text.isdigit() else code_text
            elif lower.startswith("message(") and ":" in text:
                raw_message = text.split(":", 1)[1].strip()
                parsed["message"] = _parse_json_text(raw_message, fallback=raw_message)

        if parsed["is_error"]:
            raise TencentChannelError(self._friendly_mcp_tool_error(parsed), parsed)
        return parsed

    def _friendly_mcp_tool_error(self, parsed: dict[str, Any]) -> str:
        """把 MCP tool 错误转换为可操作提示。

        Args:
            parsed: _extract_tool_result 解析出的错误内容。

        Returns:
            面向管理员和 LLM 的错误说明。
        """
        code = str(parsed.get("code") or "")
        message = parsed.get("message")
        if isinstance(message, dict):
            text = json.dumps(message, ensure_ascii=False)
        else:
            text = str(message or "无详细信息")

        if code == "8011" or "未登录" in text or "token" in text.lower():
            return "腾讯频道鉴权失败，请使用 /txcm login 重新授权，或用 /txcm token 写入有效 Token。"
        if code == "153" or "频率" in text or "rate" in text.lower():
            return "腾讯频道接口触发频率限制，请等待约 70 秒后重试。"
        if "required" in text.lower() or "参数" in text or "validation" in text.lower():
            return (
                f"腾讯频道参数校验失败：{text}。请先用 /txcm schema 查看工具 schema。"
            )
        return f"腾讯频道 MCP 工具返回错误：{text}"

    async def call_mcp_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        bypass_risk_gate: bool = False,
    ) -> dict[str, Any]:
        normalized = _normalize_tool_name(tool_name)
        if not normalized:
            raise TencentChannelError("工具名不能为空。")
        if not isinstance(arguments, dict):
            raise TencentChannelError("arguments 必须是 JSON object。")

        if not bypass_risk_gate:
            if normalized in HIGH_RISK_TOOLS and not self._cfg(
                "enable_high_risk_tools"
            ):
                raise TencentChannelError(
                    f"{normalized} 属于高风险操作，请在插件配置中启用高风险工具后再调用。"
                )
            if normalized in WRITE_TOOLS and not self._cfg("enable_write_tools"):
                raise TencentChannelError(
                    f"{normalized} 属于写操作，请在插件配置中启用写操作工具后再调用。"
                )

        response = await self._mcp_request(
            "tools/call",
            {"name": normalized, "arguments": arguments},
            token_required=True,
        )
        return self._extract_tool_result(response)

    def _extract_guilds(self, data: Any) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                keys = {str(key).lower() for key in value}
                if {"guildid", "guildname"} & keys or {
                    "uint64guildid",
                    "strguildname",
                } & keys:
                    matches.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    for item in value:
                        item_keys = {str(key).lower() for key in item}
                        if {"guildid", "guildname"} & item_keys or {
                            "uint64guildid",
                            "strguildname",
                        } & item_keys:
                            matches.extend(value)
                            return
                for item in value:
                    visit(item)

        visit(data)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for guild in matches:
            key = str(
                guild.get("guildId")
                or guild.get("guild_id")
                or guild.get("uint64GuildId")
                or guild.get("id")
                or json.dumps(guild, ensure_ascii=False, sort_keys=True)
            )
            if key not in seen:
                unique.append(guild)
                seen.add(key)
        return unique

    async def _list_guilds_payload(self) -> dict[str, Any]:
        result = await self.call_mcp_tool(
            "get_my_join_guild_info",
            DEFAULT_GUILD_LIST_ARGUMENTS,
            bypass_risk_gate=True,
        )
        return {
            "parsed": result,
            "guilds": self._extract_guilds(result.get("message")),
        }

    def _format_guilds(self, guilds: list[dict[str, Any]]) -> str:
        if not guilds:
            return "未解析到频道列表。Token 可能有效，但上游返回结构与预期不同。"

        lines = [f"已加入频道：{len(guilds)} 个"]
        for index, guild in enumerate(guilds[:20], start=1):
            name = (
                guild.get("guildName")
                or guild.get("strGuildName")
                or guild.get("name")
                or "未命名频道"
            )
            number = guild.get("guildNumber") or guild.get("strGuildNumber") or ""
            member_num = guild.get("memberNum") or guild.get("uint32MemberNum") or ""
            suffix = []
            if number:
                suffix.append(f"频道号 {number}")
            if member_num:
                suffix.append(f"{member_num} 人")
            lines.append(
                f"{index}. {name}" + (f"（{'，'.join(suffix)}）" if suffix else "")
            )
        if len(guilds) > 20:
            lines.append(f"... 还有 {len(guilds) - 20} 个未显示")
        return "\n".join(lines)

    async def _status_payload(self) -> dict[str, Any]:
        server = await self._initialize_mcp()
        tools = await self._list_mcp_tools()
        payload: dict[str, Any] = {
            "endpoint": self._cfg("mcp_endpoint"),
            "token_configured": bool(self._token()),
            "server": server.get("serverInfo", server),
            "tool_count": len(tools),
            "cli_command_count": len(CLI_COMMANDS),
            "write_tools_enabled": bool(self._cfg("enable_write_tools")),
            "high_risk_tools_enabled": bool(self._cfg("enable_high_risk_tools")),
        }
        if self._token():
            try:
                guilds_payload = await self._list_guilds_payload()
                payload["credential_probe"] = "ok"
                payload["guild_count"] = len(guilds_payload["guilds"])
            except TencentChannelError as exc:
                payload["credential_probe"] = f"failed: {exc}"
        return payload

    def _resolve_cli_command_key(self, command: str) -> str:
        """把用户输入的 CLI 命令归一化为 domain.action。

        Args:
            command: CLI 命令名，支持 feed.publish-feed、feed publish-feed 或单独 action。

        Returns:
            CLI_COMMANDS 中的标准 key。

        Raises:
            TencentChannelError: 命令不存在或单独 action 匹配到多个域。
        """
        raw = str(command or "").strip().lower()
        if raw.startswith("tencent-channel-cli "):
            raw = raw[len("tencent-channel-cli ") :].strip()
        raw = raw.replace("/", ".")
        parts = [part for part in raw.split() if part]
        if len(parts) >= 2 and parts[0] in {"feed", "manage"}:
            raw = f"{parts[0]}.{parts[1]}"
        raw = raw.replace(" ", ".")

        if "." in raw:
            domain, action = raw.split(".", 1)
            key = f"{domain}.{action.replace('_', '-')}"
            if key in CLI_COMMANDS:
                return key
        else:
            action = raw.replace("_", "-")
            matches = [key for key in CLI_COMMANDS if key.endswith(f".{action}")]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise TencentChannelError(
                    f"命令 {command} 同时匹配多个域，请写成 feed.{action} 或 manage.{action}。"
                )

        raise TencentChannelError(f"未找到 CLI 命令映射: {command}")

    def _cli_mapping_payload(self, command: str) -> dict[str, Any]:
        key = self._resolve_cli_command_key(command)
        item = dict(CLI_COMMANDS[key])
        item["command"] = key
        item["supported_by_plugin"] = bool(item.get("tool"))
        if item.get("tool"):
            item["call_note"] = (
                "可用 /txcm schema 查看 MCP schema，再用 /txcm ccall 或 txcm_call_cli_command 调用。"
            )
        else:
            item["call_note"] = (
                "这是 CLI 本地流程或快捷命令，插件提供组合工具但不模拟 CLI 交互状态机。"
            )
        return item

    def _endpoint_payload(self, topic: str = "") -> dict[str, Any]:
        topic = str(topic or "").strip().lower()
        if topic:
            key = topic.replace("-", "_")
            if key not in ENDPOINT_GUIDE:
                raise TencentChannelError(f"未找到接口参考: {topic}")
            return {key: ENDPOINT_GUIDE[key]}
        return ENDPOINT_GUIDE

    def _skill_guide_text(self, topic: str = "") -> str:
        topic = str(topic or "").strip().lower()
        sections = {
            "risk": (
                "风险规则：del_feed、delete_channel、kick_guild_member、leave_guild、"
                "modify_member_shut_up、do_comment、do_reply、deal_notice 等高风险工具默认禁用。"
                "写操作需要 enable_write_tools，高风险操作还需要 enable_high_risk_tools。"
                "管理员权限由 AstrBot 的指令和工具权限配置控制。"
            ),
            "login": (
                "登录规则：MCP 调用使用 QQ AI Connect Token，HTTP Header 为 "
                "Authorization: Bearer <token>。/txcm token 可写入 Token。/txcm login "
                "会直接请求腾讯连接设备授权端点，发送二维码/授权链接并自动轮询回写 Token。"
            ),
            "guild": (
                "频道管理：先用 txcm_list_guilds 获取当前账号频道；需要具体工具参数时，"
                "先调用 txcm_get_tool_schema，再用 txcm_call_tool 调用原始 MCP 工具。"
            ),
            "member": (
                "成员操作：@用户前必须先搜索成员获得 tiny_id，不要用昵称或 QQ 号猜测。"
                "禁言、踢人属于高风险操作。"
            ),
            "feed": (
                "帖子操作：浏览、详情、评论列表通常是读操作；发帖、改帖、删帖、评论、回复、"
                "置顶、精华、移动帖子属于写操作，其中删帖和评论/回复删除属于高风险操作。"
            ),
            "notification": (
                "通知操作：处理加入申请、私信回复等需要先读取通知列表并保留通知上下文字段。"
                "deal_notice 属于高风险操作，默认禁用。"
            ),
            "cli": (
                "CLI 对齐：/txcm cli 可列出 tencent-channel-cli 命令与 MCP tool 映射；"
                "/txcm map <domain.action> 查看单条映射；/txcm ccall <domain.action> <JSON> "
                "按 CLI 命令名定位 MCP tool。ccall 的 JSON 参数仍需使用 MCP schema。"
            ),
            "endpoint": (
                "端点规则：登录走 connect.qq.com 设备码接口；业务能力统一走 graph.qq.com MCP "
                "JSON-RPC tools/call；媒体上传会先 apply_media_upload 再访问动态 sliceupload 地址。"
            ),
            "media": (
                "媒体上传：CLI 会先 apply_media_upload 获取 uploadRsp.upload_addrs，再用内部二进制"
                "分片协议 POST 到 http://<host>:<port>/sliceupload，最后 apply_media_upload_status_sync。"
                "当前插件暴露这些 MCP 原子工具，但不复刻 sliceupload 二进制编码。"
            ),
            "shortcut": (
                "快捷命令：quick-publish、search-and-comment、delete-and-mute、search-and-join 等是"
                "CLI 本地多步流程。插件侧按原子工具组合执行，不维护 CLI resume 状态。"
            ),
        }
        if topic in sections:
            return sections[topic]
        return "\n".join(
            [
                sections["login"],
                sections["risk"],
                sections["cli"],
                sections["endpoint"],
                sections["guild"],
                sections["member"],
                sections["feed"],
                sections["notification"],
            ]
        )

    async def tool_status(self) -> str:
        return _json_dumps(await self._status_payload())

    async def tool_list_tools(self, query: str = "") -> str:
        tools = await self._list_mcp_tools()
        keyword = str(query or "").strip().lower()
        rows = []
        for tool in tools:
            name = str(tool.get("name") or "")
            desc = str(tool.get("description") or "")
            if keyword and keyword not in name.lower() and keyword not in desc.lower():
                continue
            rows.append({"name": name, "description": desc})
        return _json_dumps(rows)

    async def tool_get_tool_schema(self, tool_name: str) -> str:
        normalized = _normalize_tool_name(tool_name)
        tools = await self._list_mcp_tools()
        for tool in tools:
            if _normalize_tool_name(str(tool.get("name") or "")) == normalized:
                return _json_dumps(tool)
        raise TencentChannelError(f"未找到 MCP 工具: {tool_name}")

    async def tool_list_guilds(self) -> str:
        payload = await self._list_guilds_payload()
        return _json_dumps(payload)

    async def tool_call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        arguments_json: str = "",
    ) -> str:
        if arguments is None:
            arguments = _parse_json_text(str(arguments_json or "{}"), fallback=None)
        if not isinstance(arguments, dict):
            raise TencentChannelError(
                'arguments_json 必须是 JSON object 字符串，例如 {"guildId":"123"}。'
            )
        result = await self.call_mcp_tool(tool_name, arguments)
        return _json_dumps(result)

    async def tool_skill_guide(self, topic: str = "") -> str:
        return self._skill_guide_text(topic)

    async def tool_list_cli_commands(self, query: str = "") -> str:
        keyword = str(query or "").strip().lower()
        rows = []
        for command, item in CLI_COMMANDS.items():
            text = " ".join(
                [
                    command,
                    str(item.get("tool") or ""),
                    str(item.get("group") or ""),
                    str(item.get("risk") or ""),
                    str(item.get("description") or ""),
                    str(item.get("note") or ""),
                ]
            ).lower()
            if keyword and keyword not in text:
                continue
            rows.append(
                {
                    "command": command,
                    "tool": item.get("tool") or "",
                    "group": item.get("group"),
                    "risk": item.get("risk"),
                    "description": item.get("description"),
                    "note": item.get("note", ""),
                    "supported_by_plugin": bool(item.get("tool")),
                }
            )
        return _json_dumps(rows)

    async def tool_get_cli_mapping(self, command: str) -> str:
        return _json_dumps(self._cli_mapping_payload(command))

    async def tool_call_cli_command(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        arguments_json: str = "",
    ) -> str:
        mapping = self._cli_mapping_payload(command)
        tool_name = str(mapping.get("tool") or "")
        if not tool_name:
            raise TencentChannelError(str(mapping["call_note"]))
        if arguments is None:
            arguments = _parse_json_text(str(arguments_json or "{}"), fallback=None)
        if not isinstance(arguments, dict):
            raise TencentChannelError(
                'arguments_json 必须是 JSON object 字符串，例如 {"guildId":"123"}。'
            )
        result = await self.call_mcp_tool(tool_name, arguments)
        return _json_dumps({"mapping": mapping, "result": result})

    async def tool_endpoint_guide(self, topic: str = "") -> str:
        return _json_dumps(self._endpoint_payload(topic))

    async def _request_device_code(self) -> dict[str, Any]:
        url = str(self._cfg("device_code_request_url", "") or "").strip()
        if not url:
            raise TencentChannelError(
                "未配置设备码申请端点，请在插件配置中填写或使用 /txcm token 写入 Token。"
            )

        payload = _parse_json_text(
            str(self._cfg("login_request_payload_json", "{}") or "{}"),
            fallback={},
        )
        if not isinstance(payload, dict):
            raise TencentChannelError("login_request_payload_json 必须是 JSON object。")
        device_id = str(payload.get("device_id") or "").strip()
        if device_id:
            try:
                uuid.UUID(device_id)
            except ValueError as exc:
                raise TencentChannelError("device_id 必须是合法 UUID。") from exc
        else:
            device_id = str(uuid.uuid4())
            payload["device_id"] = device_id

        data = await self._post_json(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "X-Oidb": json.dumps(REQUEST_DEVICE_CODE_OIDB, separators=(",", ":")),
            },
        )
        result = self._unwrap_auth_gateway_response(data)
        if isinstance(result, dict):
            result.setdefault("device_id", device_id)
        return result

    async def _poll_device_token(
        self, device_code: str, device_id: str
    ) -> dict[str, Any]:
        url = str(self._cfg("device_token_poll_url", "") or "").strip()
        if not url:
            raise TencentChannelError("未配置设备码轮询端点。")
        payload = _parse_json_text(
            str(self._cfg("login_poll_payload_json", "{}") or "{}"),
            fallback={},
        )
        if not isinstance(payload, dict):
            raise TencentChannelError("login_poll_payload_json 必须是 JSON object。")
        payload["device_code"] = device_code
        payload["device_id"] = device_id
        data = await self._post_json(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "X-Oidb": json.dumps(POLL_DEVICE_TOKEN_OIDB, separators=(",", ":")),
            },
        )
        return self._unwrap_auth_gateway_response(data)

    def _unwrap_auth_gateway_response(self, data: Any) -> dict[str, Any]:
        """解包腾讯连接设备授权网关响应。

        Args:
            data: 上游返回的 JSON 对象。

        Returns:
            解包后的业务数据。

        Raises:
            TencentChannelError: 上游返回业务错误或结构异常。
        """
        if not isinstance(data, dict):
            raise TencentChannelError("腾讯频道登录端点返回格式异常。", data)

        retcode = data.get("retcode")
        if retcode not in (None, 0, "0"):
            message = data.get("message") or data.get("msg") or data.get("tipMsg")
            error = data.get("error")
            if not message and isinstance(error, dict):
                message = error.get("message")
            raise TencentChannelError(
                f"腾讯频道登录网关错误：retcode={retcode}，{message or '无详细信息'}",
                data,
            )

        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = _parse_json_text(payload, fallback={"value": payload})
        if not isinstance(payload, dict):
            raise TencentChannelError("腾讯频道登录端点 data 格式异常。", data)

        code = payload.get("code")
        if code not in (None, 0, "0"):
            message = payload.get("message") or payload.get("msg") or "无详细信息"
            raise TencentChannelError(
                f"腾讯频道登录业务错误：code={code}，{message}",
                data,
            )

        inner = payload.get("data", payload)
        if isinstance(inner, str):
            inner = _parse_json_text(inner, fallback={"value": inner})
        if not isinstance(inner, dict):
            raise TencentChannelError("腾讯频道登录业务 data 格式异常。", data)
        return inner

    def _extract_login_token(self, data: dict[str, Any]) -> str:
        for key in (
            "qq_ai_connect_token",
            "QQ_AI_CONNECT_TOKEN",
            "access_token",
            "token",
            "session_key",
            "sessionKey",
        ):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        credentials = data.get("credentials")
        if isinstance(credentials, dict):
            return self._extract_login_token(credentials)
        return ""

    async def _login_poll_loop(
        self,
        event: AstrMessageEvent,
        device_code: str,
        device_id: str,
        interval: int,
        expires_in: int,
    ) -> None:
        deadline = time.time() + max(60, expires_in)
        while time.time() < deadline:
            await asyncio.sleep(max(1, interval))
            try:
                data = await self._poll_device_token(device_code, device_id)
            except TencentChannelError as exc:
                await event.send(event.plain_result(f"腾讯频道登录轮询失败：{exc}"))
                return

            status = str(data.get("status") or "").lower()
            next_interval = data.get("interval")
            if next_interval:
                try:
                    interval = int(next_interval)
                except (TypeError, ValueError):
                    pass
            token = self._extract_login_token(data)
            if token:
                self._set_cfg("qq_ai_connect_token", token)
                self._save_config()
                await event.send(
                    event.plain_result("腾讯频道登录成功，Token 已写入插件配置。")
                )
                return
            if status in {"authorized", "success"}:
                await event.send(
                    event.plain_result("腾讯频道已授权，但轮询响应中没有 Token。")
                )
                return
            if status in {"1", "pending", "pending_authorization"}:
                continue
            if status in {"expired", "denied", "cancelled", "failed"}:
                await event.send(event.plain_result(f"腾讯频道登录失败：{status}"))
                return

        await event.send(
            event.plain_result("腾讯频道登录二维码已过期，请重新执行 /txcm login。")
        )

    def _help_text(self) -> str:
        return (
            "腾讯频道社区管理工具指令：\n"
            "/txcm status - 检查端点与 Token\n"
            "/txcm token <token> - 写入 QQ AI Connect Token\n"
            "/txcm login - 发起设备码登录并自动轮询\n"
            "/txcm tools [关键词] - 列出 MCP 工具\n"
            "/txcm cli [关键词] - 列出 CLI 命令与 MCP 映射\n"
            "/txcm map <domain.action> - 查看 CLI 命令映射\n"
            "/txcm endpoints [topic] - 查看接口参考\n"
            "/txcm schema <工具名> - 查看工具 schema\n"
            "/txcm list - 列出已加入频道\n"
            "/txcm call <工具名> <JSON> - 调用原始 MCP 工具\n"
            "/txcm ccall <domain.action> <JSON> - 按 CLI 命令名调用 MCP 工具\n"
            "/txcm guide [topic] - 查看内置 Skill 指导"
        )

    @filter.command_group("txcm")
    def txcm(self):
        """腾讯频道社区管理工具指令组。"""
        pass

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("help")
    async def txcm_help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """显示腾讯频道插件帮助。"""
        yield event.plain_result(self._help_text())

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("status")
    async def txcm_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """检查腾讯频道 MCP 状态。"""
        try:
            payload = await self._status_payload()
        except TencentChannelError as exc:
            yield event.plain_result(f"腾讯频道状态检查失败：{exc}")
            return

        lines = [
            "腾讯频道 MCP 状态",
            f"端点：{payload['endpoint']}",
            f"Token：{'已配置' if payload['token_configured'] else '未配置'}",
            f"工具数：{payload['tool_count']}",
            f"CLI 映射：{payload['cli_command_count']} 条",
            f"凭证探测：{payload.get('credential_probe', '未执行')}",
            f"写操作：{'开启' if payload['write_tools_enabled'] else '关闭'}",
            f"高风险操作：{'开启' if payload['high_risk_tools_enabled'] else '关闭'}",
        ]
        if "guild_count" in payload:
            lines.append(f"已解析频道数：{payload['guild_count']}")
        yield event.plain_result("\n".join(lines))

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("token")
    async def txcm_token(
        self,
        event: AstrMessageEvent,
        token: GreedyStr,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """写入 QQ AI Connect Token。"""
        value = str(token or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value:
            yield event.plain_result("用法：/txcm token <QQ_AI_CONNECT_TOKEN>")
            return

        self._set_cfg("qq_ai_connect_token", value)
        self._save_config()
        try:
            await self._list_guilds_payload()
            yield event.plain_result("Token 已写入配置，凭证探测成功。")
        except TencentChannelError as exc:
            yield event.plain_result(f"Token 已写入配置，但凭证探测失败：{exc}")

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("login")
    async def txcm_login(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """发起设备码登录并后台自动轮询。"""
        if self._login_task and not self._login_task.done():
            yield event.plain_result("已有腾讯频道登录轮询任务正在运行。")
            return

        try:
            data = await self._request_device_code()
        except TencentChannelError as exc:
            yield event.plain_result(str(exc))
            return

        device_code = str(data.get("device_code") or data.get("deviceCode") or "")
        if not device_code:
            yield event.plain_result("设备码申请成功，但响应中没有 device_code。")
            return
        device_id = str(data.get("device_id") or data.get("deviceId") or "")
        if not device_id:
            yield event.plain_result("设备码申请成功，但响应中没有 device_id。")
            return

        interval = int(
            data.get("interval") or self._cfg("login_poll_interval_seconds", 3)
        )
        expires_in = int(
            data.get("expires_in_s")
            or data.get("expires_in")
            or self._cfg("login_timeout_seconds", 420)
        )
        link = str(data.get("verification_uri") or data.get("verificationUri") or "")
        qr_code = str(data.get("qr_code") or data.get("qrcode") or "")

        lines = ["腾讯频道登录已创建，后台会自动轮询授权结果。"]
        if link:
            lines.append(f"授权链接：<{link}>")
        lines.append(f"有效期：{expires_in} 秒")

        if qr_code:
            try:
                base64.b64decode(qr_code, validate=False)
                yield event.chain_result([Comp.Image.fromBase64(qr_code)])
            except Exception:
                logger.warning(f"[{PLUGIN_NAME}] login qr_code is not valid base64")
        yield event.plain_result("\n".join(lines))

        self._login_task = asyncio.create_task(
            self._login_poll_loop(event, device_code, device_id, interval, expires_in)
        )

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("tools")
    async def txcm_tools(
        self,
        event: AstrMessageEvent,
        query: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """列出腾讯频道 MCP 工具。"""
        try:
            tools = await self._list_mcp_tools()
        except TencentChannelError as exc:
            yield event.plain_result(f"获取工具列表失败：{exc}")
            return

        keyword = str(query or "").strip().lower()
        lines = []
        for tool in tools:
            name = str(tool.get("name") or "")
            desc = str(tool.get("description") or "")
            if keyword and keyword not in name.lower() and keyword not in desc.lower():
                continue
            lines.append(f"- {name}: {desc[:80]}")
            if len(lines) >= 60:
                lines.append("... 已截断，请加关键词过滤")
                break
        yield event.plain_result("\n".join(lines) if lines else "未找到匹配工具。")

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("cli")
    async def txcm_cli(
        self,
        event: AstrMessageEvent,
        query: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """列出 CLI 命令与 MCP 映射。"""
        keyword = str(query or "").strip().lower()
        lines = []
        for command, item in CLI_COMMANDS.items():
            text = " ".join(
                [
                    command,
                    str(item.get("tool") or ""),
                    str(item.get("group") or ""),
                    str(item.get("risk") or ""),
                    str(item.get("description") or ""),
                    str(item.get("note") or ""),
                ]
            ).lower()
            if keyword and keyword not in text:
                continue
            tool = item.get("tool") or "本地流程"
            lines.append(
                f"- {command} -> {tool} [{item.get('group')}/{item.get('risk')}] "
                f"{item.get('description')}"
            )
            if item.get("note"):
                lines.append(f"  {item['note']}")
            if len(lines) >= 90:
                lines.append("... 已截断，请加关键词过滤")
            break
        yield event.plain_result("\n".join(lines) if lines else "未找到匹配命令。")

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("map")
    async def txcm_map(
        self,
        event: AstrMessageEvent,
        command: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看单条 CLI 命令映射。"""
        if not str(command or "").strip():
            yield event.plain_result("用法：/txcm map feed.publish-feed")
            return
        try:
            payload = self._cli_mapping_payload(str(command))
        except TencentChannelError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(_json_dumps(payload))

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("endpoints")
    async def txcm_endpoints(
        self,
        event: AstrMessageEvent,
        topic: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看接口参考。"""
        try:
            payload = self._endpoint_payload(str(topic or ""))
        except TencentChannelError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(_json_dumps(payload))

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("schema")
    async def txcm_schema(
        self,
        event: AstrMessageEvent,
        tool_name: str,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看腾讯频道 MCP 工具 schema。"""
        try:
            text = await self.tool_get_tool_schema(tool_name)
        except TencentChannelError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(text)

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("list")
    async def txcm_list(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """列出当前账号已加入频道。"""
        try:
            payload = await self._list_guilds_payload()
        except TencentChannelError as exc:
            yield event.plain_result(f"获取频道列表失败：{exc}")
            return
        yield event.plain_result(self._format_guilds(payload["guilds"]))

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("call")
    async def txcm_call(
        self,
        event: AstrMessageEvent,
        tool_name: str,
        raw_json: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """调用腾讯频道 MCP 原始工具。"""
        arguments = _parse_json_text(str(raw_json or "{}"), fallback=None)
        if not isinstance(arguments, dict):
            yield event.plain_result(
                '参数必须是 JSON object，例如：/txcm call get_guild_info {"guildId":"..."}'
            )
            return

        try:
            result = await self.call_mcp_tool(tool_name, arguments)
        except TencentChannelError as exc:
            yield event.plain_result(f"调用失败：{exc}")
            return
        yield event.plain_result(_json_dumps(result))

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("ccall")
    async def txcm_ccall(
        self,
        event: AstrMessageEvent,
        command: str,
        raw_json: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """按 CLI 命令名定位 MCP 工具并调用。"""
        arguments = _parse_json_text(str(raw_json or "{}"), fallback=None)
        if not isinstance(arguments, dict):
            yield event.plain_result(
                '参数必须是 JSON object，例如：/txcm ccall feed.get-feed-detail {"feedId":"..."}'
            )
            return

        try:
            result = await self.tool_call_cli_command(command, arguments)
        except TencentChannelError as exc:
            yield event.plain_result(f"调用失败：{exc}")
            return
        yield event.plain_result(result)

    @txcm.custom_filter(filter.PermissionTypeFilter, filter.PermissionType.ADMIN)
    @txcm.command("guide")
    async def txcm_guide(
        self,
        event: AstrMessageEvent,
        topic: GreedyStr = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看内置 Skill 指导。"""
        yield event.plain_result(self._skill_guide_text(str(topic or "")))
