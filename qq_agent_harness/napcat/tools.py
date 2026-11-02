"""NapCat 能力目录与类型化内置工具。发送只有一个入口，读写来源由可信上下文绑定。"""

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from ..contracts import (
    JsonObject,
    Model,
    ToolBinding,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)
from ..settings import NapCatSettings
from . import arguments as a
from .connection import (
    NapCatError,
    NapCatUnavailable,
    object_value,
    scoped_history,
    scoped_message,
)
from .media import MediaReader
from .media_sources import non_message_segment

BASELINE_VERSION = "v4.18.41"
BASELINE_COMMIT = "859eb530c5801577633c2c3ae871d23586edf281"
BEIJING = timezone(timedelta(hours=8))

# 控制台展示的能力分类名称，按展示顺序排列；权限配置和模型侧仍使用分类 ID。
CATEGORY_LABELS = {
    "messages": "消息",
    "media": "媒体",
    "members": "群成员",
    "groups": "群聊",
    "notices": "群公告与待办",
    "files": "群文件",
    "albums": "群相册",
    "contacts": "好友与资料",
    "account": "机器人账号",
    "utilities": "实用工具",
    "system": "系统控制",
}


@dataclass(frozen=True)
class ActionSpec:
    action: str
    summary: str
    model: type[BaseModel]
    category: str
    scope: Literal["both", "group", "private", "account"] = "group"
    deferred: bool = False
    message_bound: bool = False
    inject_peer: bool = True
    filter_groups: bool = False

    @property
    def name(self) -> str:
        return "qq__" + self.action.lstrip("_.")


# 只有明确列在目录中的业务动作可被调用；不能从模型输入选择原始 action。
ACTION_SPECS = (
    ActionSpec("get_login_info", "查询机器人账号的 QQ 号与昵称", a.Empty, "account", "account"),
    ActionSpec("get_status", "查询 QQ 在线状态", a.Empty, "account", "account", deferred=True),
    ActionSpec(
        "get_robot_uin_range",
        "查询官方机器人账号号段范围",
        a.Empty,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "nc_get_packet_status",
        "查询 NapCat 扩展协议连接状态",
        a.Empty,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_version_info",
        "查询 NapCat 版本信息",
        a.Empty,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "can_send_image", "检查当前是否支持发送图片", a.Empty, "account", "account", deferred=True
    ),
    ActionSpec(
        "can_send_record", "检查当前是否支持发送语音", a.Empty, "account", "account", deferred=True
    ),
    ActionSpec("get_friend_list", "获取好友列表", a.Fresh, "contacts", "account"),
    ActionSpec(
        "get_friends_with_category",
        "获取按分组整理的好友列表",
        a.Empty,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_unidirectional_friend_list",
        "获取单向好友列表",
        a.Empty,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_recent_contact",
        "获取最近联系人列表",
        a.Count,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec("get_group_list", "获取机器人加入的群列表", a.Fresh, "account", "account"),
    ActionSpec(
        "get_group_info",
        "查询群名称、人数、容量和群主 QQ 号",
        a.Empty,
        "groups",
    ),
    ActionSpec(
        "get_group_info_ex",
        "读取群扩展协议字段（查群主或群介绍请用 get_group_detail_info）",
        a.Empty,
        "groups",
        deferred=True,
    ),
    ActionSpec(
        "get_group_detail_info",
        "查询群主 QQ 号、群介绍、加群问题和群规模",
        a.Empty,
        "groups",
    ),
    ActionSpec("get_group_share_link", "获取当前群的分享链接", a.Empty, "groups", deferred=True),
    ActionSpec(
        "get_group_member_list",
        "按身份、昵称、群名片、QQ 号或头衔查找群成员（已知 QQ 号时可用 get_group_member_info）",
        a.GroupMemberList,
        "members",
    ),
    ActionSpec(
        "get_group_member_info",
        "查询指定群成员的群名片、头衔、身份、入群与最后发言时间，可选附带 QQ 公开资料和在线状态",
        a.MemberInfo,
        "members",
    ),
    ActionSpec(
        "get_stranger_info",
        "查询指定 QQ 号的公开资料（昵称、个性签名、注册时间、等级、性别与地区）",
        a.UserFresh,
        "members",
        "account",
    ),
    ActionSpec("nc_get_user_status", "查询指定 QQ 号的在线状态", a.User, "members", "account"),
    ActionSpec("get_group_honor_info", "查看群荣誉榜单（龙王、群聊之火等）", a.Honor, "groups"),
    ActionSpec("get_essence_msg_list", "查看群精华消息列表", a.Empty, "messages"),
    ActionSpec(
        "get_group_at_all_remain",
        "查看今日剩余 @全体成员 次数",
        a.Empty,
        "groups",
        deferred=True,
    ),
    ActionSpec("get_group_shut_list", "查看当前群被禁言成员列表", a.Empty, "groups", deferred=True),
    ActionSpec(
        "get_group_signed_list", "查看当前群今日已打卡成员", a.Empty, "groups", deferred=True
    ),
    ActionSpec("_get_group_notice", "查看群公告列表", a.Empty, "notices"),
    ActionSpec(
        "get_group_system_msg",
        "查看当前群的加群申请与邀请通知",
        a.Empty,
        "groups",
        deferred=True,
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "get_group_ignored_notifies",
        "查看当前群已忽略的系统通知",
        a.Empty,
        "groups",
        deferred=True,
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "get_group_ignore_add_request",
        "查看当前群已忽略的加群申请",
        a.Empty,
        "groups",
        deferred=True,
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec("set_group_ban", "禁言或解除禁言群成员", a.MemberBan, "groups", deferred=True),
    ActionSpec("set_group_whole_ban", "开启或关闭全员禁言", a.Enabled, "groups", deferred=True),
    ActionSpec("set_group_kick", "将指定成员移出当前群", a.MemberKick, "groups", deferred=True),
    ActionSpec(
        "set_group_kick_members", "批量将成员移出当前群", a.MembersKick, "groups", deferred=True
    ),
    ActionSpec(
        "set_group_admin",
        "设置或取消群管理员",
        a.MemberAdmin,
        "groups",
        deferred=True,
    ),
    ActionSpec("set_group_card", "修改群成员的群名片", a.MemberCard, "groups"),
    ActionSpec(
        "set_group_special_title",
        "修改群成员的专属头衔",
        a.MemberTitle,
        "groups",
    ),
    ActionSpec("set_group_name", "修改当前群名称", a.GroupName, "groups", deferred=True),
    ActionSpec(
        "set_group_remark",
        "修改当前群的本地备注",
        a.GroupRemark,
        "groups",
        deferred=True,
    ),
    ActionSpec("set_group_leave", "退出或解散当前群", a.GroupLeave, "groups", deferred=True),
    ActionSpec("set_group_sign", "在当前群签到打卡", a.Empty, "groups"),
    ActionSpec("_send_group_notice", "发布群公告", a.GroupNotice, "notices", deferred=True),
    ActionSpec("_del_group_notice", "删除指定群公告", a.NoticeId, "notices", deferred=True),
    ActionSpec(
        "set_essence_msg",
        "将群消息设为精华消息",
        a.MessageId,
        "messages",
        message_bound=True,
        inject_peer=False,
    ),
    ActionSpec(
        "delete_essence_msg",
        "取消群消息的精华标记",
        a.MessageId,
        "messages",
        message_bound=True,
        inject_peer=False,
    ),
    ActionSpec(
        "delete_msg",
        "撤回当前会话中的指定消息",
        a.MessageId,
        "messages",
        "both",
        False,
        True,
        False,
    ),
    ActionSpec(
        "set_msg_emoji_like",
        "给消息添加或取消贴表情回应（set=true 添加，set=false 取消）",
        a.Reaction,
        "messages",
        "both",
        False,
        True,
        False,
    ),
    ActionSpec(
        "mark_msg_as_read",
        "将当前会话的指定消息标为已读",
        a.MessageId,
        "messages",
        "both",
        True,
        True,
        False,
    ),
    ActionSpec(
        "get_group_file_system_info",
        "查询群文件空间用量与文件总数",
        a.Empty,
        "files",
        deferred=True,
    ),
    ActionSpec("get_group_root_files", "查看群文件根目录下的文件与文件夹", a.Empty, "files"),
    ActionSpec("get_group_files_by_folder", "查看指定群文件夹内的文件列表", a.Folder, "files"),
    ActionSpec("get_group_file_url", "获取群文件的下载链接", a.FileId, "files"),
    ActionSpec(
        "create_group_file_folder",
        "在群文件中新建文件夹",
        a.FolderCreate,
        "files",
        deferred=True,
    ),
    ActionSpec("delete_group_file", "删除指定的群文件", a.GroupFile, "files", deferred=True),
    ActionSpec("delete_group_folder", "删除指定的群文件夹", a.Folder, "files", deferred=True),
    ActionSpec("move_group_file", "移动群文件到指定目录", a.MoveFile, "files", deferred=True),
    ActionSpec("rename_group_file", "重命名群文件", a.RenameFile, "files", deferred=True),
    ActionSpec("send_like", "给指定 QQ 号的资料卡点赞", a.Like, "contacts", "account"),
    ActionSpec(
        "set_friend_remark",
        "修改好友备注",
        a.FriendRemark,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec("delete_friend", "删除指定好友", a.User, "contacts", "account", deferred=True),
    ActionSpec(
        "set_friend_add_request",
        "同意或拒绝好友申请",
        a.FriendRequest,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_doubt_friends_add_request",
        "查看被过滤的可疑好友申请",
        a.Empty,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_online_clients",
        "查看当前账号同时在线的设备列表",
        a.Empty,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_self_longnick",
        "修改机器人个性签名",
        a.LongNick,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_online_status",
        "修改机器人在线状态",
        a.OnlineStatus,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_diy_online_status",
        "修改机器人自定义在线状态",
        a.DiyStatus,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_qq_profile",
        "修改机器人昵称、个人介绍或性别",
        a.QQProfile,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "fetch_custom_face",
        "获取账号收藏表情的图片链接列表",
        a.Count,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_collection_list",
        "查看账号 QQ 收藏列表",
        a.Collection,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_profile_like",
        "查看谁给机器人资料卡点了赞",
        a.Signed,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_ai_characters",
        "查询群 AI 语音可用声线列表（拿到 character 后可在 send_message 中发 ai_voice）",
        a.AICharacters,
        "media",
    ),
    ActionSpec(
        "get_ai_record",
        "生成 AI 语音音频资源（仅在需要读取音频数据时使用；直接发送语音请用 send_message 的 ai_voice）",
        a.AIRecord,
        "media",
        deferred=True,
    ),
    ActionSpec("get_qun_album_list", "查看群相册列表", a.Empty, "albums", deferred=True),
    ActionSpec(
        "get_group_album_media_list",
        "查看指定群相册内的照片与视频",
        a.AlbumMedia,
        "albums",
        deferred=True,
    ),
    ActionSpec(
        "translate_en2zh",
        "将英文词句翻译为中文",
        a.Words,
        "utilities",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "check_url_safely",
        "查询 QQ 对指定链接的安全风险评级",
        a.CheckUrl,
        "utilities",
        "account",
        deferred=True,
    ),
)


def filtered_groups(value: JsonValue, group_id: str) -> JsonValue:
    if isinstance(value, list):
        return [
            filtered_groups(item, group_id)
            for item in value
            if isinstance(item, dict) and str(item.get("group_id", "")) == group_id
        ]
    if isinstance(value, dict):
        return {
            key: filtered_groups(item, group_id)
            for key, item in value.items()
            if isinstance(item, (list, dict))
        }
    return None


async def invoke(
    context: ToolContext,
    action: str,
    params: JsonObject,
    *,
    count_send: bool = False,
) -> ToolResult:
    if context.cancelled():
        return ToolResult(status="cancelled", message="当前轮次已取消，动作未提交")
    denied = context.recheck()
    if denied is not None:
        return denied
    if count_send:
        context.send_attempts += 1
    try:
        data = await context.napcat.call(action, params)
        return ToolResult.success(data)
    except NapCatError as exc:
        if count_send and re.search(
            r"Timeout: NTEvent\s+serviceAndMethod:NodeIKernelMsgService/sendMsg\s+"
            r"ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate\b",
            str(exc),
        ):
            # 上游已调用 sendMsg，只是没等到匹配的成功通知，不能断言未送达。
            return ToolResult(
                status="unknown",
                error_type=f"NapCat:{exc.code}",
                message="消息发送请求已提交，但等待回执超时；送达状态未确认，请勿重复发送。",
            )
        return ToolResult.failure(str(exc), f"NapCat:{exc.code}")
    except NapCatUnavailable as exc:
        if not exc.submitted and count_send:
            context.send_attempts -= 1
        return ToolResult(
            status="unknown" if count_send and exc.submitted else "error",
            error_type="NapCatUnavailable",
            message=str(exc),
        )
    except (TimeoutError, ConnectionError) as exc:
        return ToolResult(
            status="unknown" if count_send else "error",
            error_type=type(exc).__name__,
            message="消息发送已提交但未收到回执，请勿直接重发。"
            if count_send
            else "NapCat 连接失败或响应超时",
        )


MEMBER_FIELDS = (
    "user_id",
    "nickname",
    "card",
    "role",
    "title",
    "sex",
    "age",
    "area",
    "level",
    "join_time",
    "last_sent_time",
    "title_expire_time",
    "shut_up_timestamp",
)


def member_page(value: JsonValue, request: a.GroupMemberList) -> JsonObject:
    if not isinstance(value, list):
        raise ValueError("NapCat 未返回有效的群成员列表")
    members = [item for item in value if isinstance(item, dict)]
    query = (request.query or "").strip().casefold()
    matched = [
        member
        for member in members
        if (request.role is None or member.get("role") == request.role)
        and (
            not query
            or any(
                query in str(member.get(field, "")).casefold()
                for field in ("nickname", "card", "user_id", "title")
            )
        )
    ]
    page = matched[request.offset : request.offset + request.limit]
    end = request.offset + len(page)
    return {
        "total": len(members),
        "matched": len(matched),
        "items": [{key: member[key] for key in MEMBER_FIELDS if key in member} for member in page],
        "next_offset": end if end < len(matched) else None,
    }


def group_summary(value: JsonValue, group_id: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("NapCat 未返回有效的群资料")
    fields = {
        "group_name": ("group_name", "groupName"),
        "member_count": ("member_count", "memberNum"),
        "max_member_count": ("max_member_count", "maxMemberNum"),
        "group_memo": ("group_memo", "groupMemo"),
        "group_remark": ("group_remark",),
        "group_all_shut": ("group_all_shut",),
        "group_question": ("group_question", "groupQuestion"),
        "group_create_time": ("group_create_time", "groupCreateTime"),
        "group_class": ("group_class", "groupClassExt"),
    }
    result: JsonObject = {"group_id": group_id, "owner_id": None}
    for key, aliases in fields.items():
        for alias in aliases:
            item = value.get(alias)
            if isinstance(item, (str, int, float, bool)):
                result[key] = item
                break
    for alias in ("owner_id", "ownerUin", "group_owner"):
        owner = value.get(alias)
        if (
            isinstance(owner, (str, int))
            and not isinstance(owner, bool)
            and str(owner).isdigit()
            and int(owner) > 0
        ):
            result["owner_id"] = str(owner)
            break
    return result


async def additional_query(
    context: ToolContext, action: str, arguments: JsonObject, params: JsonObject
) -> ToolResult:
    denied = context.check_tool("qq__" + action, arguments)
    if denied is not None:
        return denied
    return await invoke(context, action, params)


async def complete_group_owner(context: ToolContext, summary: JsonObject) -> ToolResult | None:
    if summary.get("owner_id"):
        return None
    request = a.GroupMemberList(role="owner", limit=1)
    response = await additional_query(
        context,
        "get_group_member_list",
        request.model_dump(mode="json"),
        {"group_id": context.target.peer_id, "no_cache": request.no_cache},
    )
    if response.status == "cancelled":
        return response
    if response.status != "ok":
        summary["owner_lookup"] = {"status": response.status, "error_type": response.error_type}
        return None
    if not isinstance(response.data, list):
        summary["owner_lookup"] = {"status": "error", "error_type": "InvalidResponse"}
        return None
    page = member_page(response.data, request)
    members = page["items"]
    if isinstance(members, list) and members and isinstance(members[0], dict):
        owner = members[0].get("user_id")
        if (
            isinstance(owner, (str, int))
            and not isinstance(owner, bool)
            and str(owner).isdigit()
            and int(owner) > 0
        ):
            summary["owner_id"] = str(owner)
    if not summary["owner_id"]:
        summary["owner_lookup"] = {"status": "unavailable"}
    return None


async def complete_member(
    context: ToolContext, result: ToolResult, request: a.MemberInfo
) -> ToolResult:
    if not isinstance(result.data, dict):
        raise ValueError("NapCat 未返回有效的群成员资料")
    member = {key: result.data[key] for key in MEMBER_FIELDS if key in result.data}
    member["avatar"] = {"tool": "qq__read_media", "user_id": request.user_id}
    queries: tuple[tuple[bool, str, str, JsonObject], ...] = (
        (
            request.include_profile,
            "get_stranger_info",
            "profile",
            {"user_id": request.user_id, "no_cache": request.no_cache},
        ),
        (request.include_status, "nc_get_user_status", "status", {"user_id": request.user_id}),
    )
    for include, action, field, params in queries:
        if not include:
            continue
        response = await additional_query(context, action, params, params)
        if response.status == "cancelled":
            return response
        if response.status == "ok":
            member[field] = response.data
        else:
            member[field + "_error"] = {
                "status": response.status,
                "error_type": response.error_type,
            }
    result.data = member
    return result


ACTION_TITLES = {
    "get_group_info": "查询群基本资料",
    "get_group_info_ex": "查询群扩展标识",
    "get_group_detail_info": "查询群详细资料",
    "get_group_member_list": "查找群成员",
    "get_group_member_info": "查询成员资料",
    "get_stranger_info": "查询 QQ 公开资料",
    "get_group_ignored_notifies": "读取被忽略的群通知",
    "get_group_ignore_add_request": "读取被忽略的加群请求",
    "set_essence_msg": "设置精华消息",
    "delete_essence_msg": "取消精华消息",
    "set_msg_emoji_like": "添加或取消表情回应",
    "fetch_custom_face": "浏览收藏表情",
    "fetch_custom_face_detail": "读取收藏表情资源信息",
    "delete_custom_face": "删除收藏表情",
    "get_ai_characters": "查询 AI 语音角色",
    "get_ai_record": "生成 AI 语音资源",
    "get_emoji_likes": "读取群消息指定表情回应者",
    "fetch_emoji_like": "分页读取指定表情回应者",
    "set_group_todo": "设置群待办",
    "complete_group_todo": "完成群待办",
    "cancel_group_todo": "取消群待办",
    "set_group_album_media_like": "点赞群相册媒体",
    "cancel_group_album_media_like": "取消群相册媒体点赞",
}


def action_tool(spec: ActionSpec) -> ToolDefinition:
    async def handle(context: ToolContext, request: BaseModel) -> ToolResult:
        params: JsonObject = request.model_dump(mode="json", exclude_none=True)
        if isinstance(request, a.GroupMemberList):
            params = {"no_cache": params["no_cache"]}
        elif isinstance(request, a.MemberInfo):
            params = {"user_id": params["user_id"], "no_cache": params["no_cache"]}
        if spec.message_bound:
            await scoped_message(context.napcat, context.target, str(params["message_id"]))
        if spec.inject_peer:
            if spec.scope == "group":
                params["group_id"] = context.target.peer_id
            elif spec.scope == "private":
                params["user_id"] = context.target.peer_id
        result = await invoke(context, spec.action, params)
        if result.status == "ok" and spec.filter_groups:
            result.data = filtered_groups(result.data, context.target.peer_id)
        if result.status != "ok":
            return result
        if isinstance(request, a.GroupMemberList):
            result.data = member_page(result.data, request)
        elif spec.action in ("get_group_info", "get_group_detail_info"):
            result.data = group_summary(result.data, context.target.peer_id)
            interrupted = await complete_group_owner(context, result.data)
            if interrupted:
                return interrupted
            avatar: JsonObject = {"tool": "qq__read_media", "group_avatar": True}
            if context.target != context.scope:
                avatar["target_group_id"] = context.target.peer_id
            result.data["avatar"] = avatar
        elif isinstance(request, a.MemberInfo):
            return await complete_member(context, result, request)
        return result

    return ToolDefinition(
        spec.name,
        spec.summary,
        spec.category,
        model_binding(spec.model, handle),
        scope=spec.scope,
        cross_scope=spec.scope in ("both", "group", "private"),
        title=ACTION_TITLES.get(spec.action, spec.summary),
        deferred=spec.deferred,
    )


def forward_ids(value: JsonValue) -> list[str]:
    found: list[str] = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(reversed(item))
        elif isinstance(item, dict):
            data = item.get("data")
            if item.get("type") == "forward" and isinstance(data, dict) and data.get("id"):
                identifier = str(data["id"])
                if identifier not in found:
                    found.append(identifier)
            else:
                pending.extend(reversed(list(item.values())))
    return found


async def forward_bundle(
    context: ToolContext,
    message_id: str,
    *,
    forward_path: list[str] | None = None,
    offset: int = 0,
    limit: int | None = 100,
) -> JsonObject:
    message = await scoped_message(context.napcat, context.target, message_id)
    current = message.get("message", [])
    errors: list[JsonValue] = []

    async def load(identifier: str) -> JsonValue:
        try:
            data = object_value(
                await context.napcat.call("get_forward_msg", {"message_id": identifier})
            )
            return data.get("messages", [])
        except (NapCatError, TimeoutError, ConnectionError) as error:
            errors.append(
                {
                    "forward_id": identifier,
                    "error_type": type(error).__name__,
                    "message": "此转发读取失败，可按原路径重新查询",
                }
            )
            return []

    ancestry: list[str] = []
    for identifier in forward_path or []:
        if identifier in ancestry:
            raise ValueError("转发路径存在循环")
        if identifier not in forward_ids(current):
            raise ValueError("转发路径不属于当前消息")
        ancestry.append(identifier)
        current = await load(identifier)
    bundles: list[tuple[list[str], JsonValue]] = []
    if ancestry:
        bundles.append((ancestry, current))
    else:
        for identifier in forward_ids(current):
            bundles.append(([identifier], await load(identifier)))
    nodes: list[JsonValue] = []
    paths: list[JsonValue] = []
    for parent, values in bundles:
        if isinstance(values, list):
            nodes.extend(values)
        for identifier in forward_ids(values):
            if identifier not in parent:
                paths.append([*parent, identifier])
    page = nodes[offset:] if limit is None else nodes[offset : offset + limit]
    return {
        "messages": page,
        "total": len(nodes),
        "next_offset": offset + len(page) if offset + len(page) < len(nodes) else None,
        "forward_paths": paths,
        "errors": errors,
    }


@dataclass
class MediaSelection:
    segments: list[JsonObject]
    indexes: list[int]
    errors: list[JsonValue]
    total: int
    next_offset: int | None
    forward_paths: JsonValue


async def media_segments(context: ToolContext, request: a.ReadMedia) -> MediaSelection:
    result: list[JsonObject] = []
    errors: list[JsonValue] = []
    paths: JsonValue = []

    def collect(value: JsonValue) -> None:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(reversed(item))
            elif isinstance(item, dict):
                if item.get("type") in ("image", "record", "video", "file"):
                    result.append(item)
                else:
                    if item.get("type") == "forward":
                        data = item.get("data")
                        # 有原生 ID 的转发统一由 forward_bundle 展开，不能同时读取内联副本。
                        if (
                            not request.include_forwards
                            or isinstance(data, dict)
                            and data.get("id")
                        ):
                            continue
                    for key in ("message", "content", "messages", "data"):
                        if key in item:
                            pending.append(item[key])
                            break

    source = non_message_segment(context.target, request)
    if source is not None:
        result.append(source)
    else:
        assert request.message_id is not None
        message = await scoped_message(context.napcat, context.target, request.message_id)
        if not request.forward_path:
            collect(message.get("message", []))
        if request.include_forwards or request.forward_path:
            bundle = await forward_bundle(
                context, request.message_id, forward_path=request.forward_path, limit=None
            )
            collect(bundle.get("messages"))
            paths = bundle.get("forward_paths", [])
            failures = bundle.get("errors")
            if isinstance(failures, list):
                errors.extend(failures)
    total = len(result)
    indexes = request.indexes
    if indexes is not None:
        if any(index < 0 or index >= total for index in indexes):
            raise ValueError("媒体索引超出当前消息可读取范围")
        next_offset = None
    else:
        indexes = list(range(request.offset, min(total, request.offset + request.limit)))
        end = request.offset + len(indexes)
        next_offset = end if end < total else None
    return MediaSelection(
        [result[index] for index in indexes], indexes, errors, total, next_offset, paths
    )


class SaveMedia(a.ReadMedia):
    destination: str = Field(
        min_length=1, description="共享沙箱中的目标相对文件路径；只保存一个媒体"
    )


class GroupAddOption(Model):
    add_type: int = Field(description="QQ 平台加群方式编号")
    group_question: str | None = Field(default=None, description="加群问题")
    group_answer: str | None = Field(default=None, description="加群答案")


class InvitePolicy(Model):
    policy: Literal["disabled", "require_approval", "no_approval", "no_approval_under_100"] = Field(
        description="QQ 群成员邀请策略"
    )


class MemberPermissions(Model):
    allow_member_upload_album: bool | None = Field(
        default=None, description="QQ 平台是否允许成员上传相册"
    )
    allow_member_temporary_session: bool | None = Field(
        default=None, description="QQ 平台是否允许成员临时会话"
    )
    allow_member_create_group: bool | None = Field(
        default=None, description="QQ 平台是否允许成员创建新群"
    )


class HistoryVisibility(Model):
    visible: bool = Field(description="是否允许新群成员查看近期历史")


EXTRA_SPECS = (
    ActionSpec(
        "_mark_all_as_read",
        "将账号下所有会话标为已读",
        a.Empty,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_restart",
        "重启 NapCat 服务",
        a.Empty,
        "system",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "bot_exit",
        "退出 NapCat 进程",
        a.Empty,
        "system",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "clean_cache",
        "清理 NapCat 与 QQ 媒体缓存",
        a.Empty,
        "system",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "fetch_custom_face_detail",
        "查询收藏表情的链接与 resId（删除收藏表情时需传入该 resId）",
        a.Count,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "delete_custom_face",
        "按 resId 删除收藏表情",
        a.DeleteFaces,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "get_emoji_likes",
        "查看群消息指定表情回应的用户列表",
        a.ReactionList,
        "messages",
        deferred=True,
        message_bound=True,
    ),
    ActionSpec(
        "fetch_emoji_like",
        "分页查看消息指定表情回应的用户列表",
        a.EmojiPage,
        "messages",
        "both",
        True,
        True,
        False,
    ),
    ActionSpec(
        "create_collection",
        "新增一条 QQ 收藏",
        a.CreateCollection,
        "account",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_doubt_friends_add_request",
        "同意被过滤的可疑好友申请",
        a.DoubtRequest,
        "contacts",
        "account",
        deferred=True,
    ),
    ActionSpec(
        "set_input_status",
        "在私聊窗口显示“对方正在输入…”状态",
        a.InputStatus,
        "messages",
        "private",
        deferred=True,
    ),
    ActionSpec(
        "set_group_todo",
        "将指定群消息设为群待办",
        a.GroupTodo,
        "notices",
        deferred=True,
        message_bound=True,
    ),
    ActionSpec(
        "complete_group_todo",
        "将群待办标为已完成",
        a.GroupTodo,
        "notices",
        deferred=True,
        message_bound=True,
    ),
    ActionSpec(
        "cancel_group_todo",
        "取消群待办",
        a.GroupTodo,
        "notices",
        deferred=True,
        message_bound=True,
    ),
    ActionSpec(
        "set_group_album_media_like",
        "给群相册照片或视频点赞",
        a.AlbumLike,
        "albums",
        deferred=True,
    ),
    ActionSpec(
        "cancel_group_album_media_like",
        "取消群相册照片或视频点赞",
        a.AlbumLike,
        "albums",
        deferred=True,
    ),
    ActionSpec(
        "del_group_album_media",
        "删除群相册中的照片或视频",
        a.AlbumDelete,
        "albums",
        deferred=True,
    ),
    ActionSpec(
        "do_group_album_comment",
        "评论群相册中的照片或视频",
        a.AlbumComment,
        "albums",
        deferred=True,
    ),
    ActionSpec(
        "set_group_add_option", "设置群加群方式与验证问题", GroupAddOption, "groups", deferred=True
    ),
    ActionSpec(
        "set_group_member_invite_policy",
        "设置群成员邀请他人入群的规则",
        InvitePolicy,
        "groups",
        deferred=True,
    ),
    ActionSpec(
        "set_group_member_permissions",
        "设置群内相册上传、临时会话等权限开关",
        MemberPermissions,
        "groups",
        deferred=True,
    ),
    ActionSpec(
        "set_group_new_member_history_visibility",
        "设置新入群成员是否可查看近期历史消息",
        HistoryVisibility,
        "groups",
        deferred=True,
    ),
)


def build_tools(settings: NapCatSettings | None = None) -> list[ToolDefinition]:
    reader = MediaReader(settings)
    tools = [action_tool(spec) for spec in (*ACTION_SPECS, *EXTRA_SPECS)]

    def avatar_tool(group: bool) -> ToolDefinition:
        action = "set_group_portrait" if group else "set_qq_avatar"

        async def avatar(context: ToolContext, source: a.ImageSource) -> ToolResult:
            if source.generated_id:
                generated = context.generated_images.get(source.generated_id)
                if generated is None:
                    raise ValueError("指定的 generated_id 不存在或已过期")
                content = generated.content
            elif source.workspace_path:
                if not context.read_workspace:
                    raise ValueError("共享工作区不可用")
                content = await context.read_workspace(source.workspace_path)
            else:
                content, _ = await reader.download(source.url or "")
            if len(content) > reader.settings.media_max_bytes:
                raise ValueError("头像超过媒体大小限制")
            params: JsonObject = {"file": "base64://" + base64.b64encode(content).decode("ascii")}
            if group:
                params["group_id"] = context.target.peer_id
            return await invoke(context, action, params)

        return ToolDefinition(
            "qq__" + action,
            "修改当前群头像（支持工作区文件、公网图片 URL 或 generated_id）。"
            if group
            else "修改机器人账号头像（支持工作区文件、公网图片 URL 或 generated_id）。",
            "groups" if group else "account",
            model_binding(a.ImageSource, avatar),
            scope="group" if group else "account",
            cross_scope=group,
            title="更新当前群头像" if group else "更新机器人头像",
            deferred=True,
        )

    tools.extend((avatar_tool(True), avatar_tool(False)))

    async def history(context: ToolContext, request: a.History) -> ToolResult:
        if request.before_message_id:
            await scoped_message(context.napcat, context.target, request.before_message_id)
        messages = await scoped_history(
            context.napcat, context.target, request.count, request.before_message_id
        )
        start: float | None = None
        end: float | None = None
        if request.minutes:
            start = (datetime.now(BEIJING) - timedelta(minutes=request.minutes)).timestamp()
        if request.start_time and request.end_time:
            start = (
                datetime.strptime(request.start_time, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=BEIJING)
                .timestamp()
            )
            end = (
                datetime.strptime(request.end_time, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=BEIJING)
                .timestamp()
            )
            if end < start:
                raise ValueError("结束时间不能早于开始时间")
        selected = [
            item
            for item in messages
            if (start is None or item.time >= start) and (end is None or item.time <= end)
        ]
        return ToolResult.success(
            {
                "messages": [item.model_dump(mode="json") for item in selected],
                "complete": False,
                "coverage": "返回近期消息记录",
            }
        )

    async def get_message(context: ToolContext, request: a.MessageId) -> ToolResult:
        return ToolResult.success(
            await scoped_message(context.napcat, context.target, request.message_id)
        )

    async def get_forward(context: ToolContext, request: a.Forward) -> ToolResult:
        return ToolResult.success(
            await forward_bundle(
                context,
                request.message_id,
                forward_path=request.forward_path,
                offset=request.offset,
                limit=request.limit,
            )
        )

    async def read_media(context: ToolContext, request: a.ReadMedia) -> ToolResult:
        selection = await media_segments(context, request)
        batch = await reader.read_many(selection.segments, context.napcat)
        return ToolResult.success(
            {
                "media": [
                    {"index": selection.indexes[index], **asset.public()}
                    for index, asset in batch.media
                ],
                "errors": [
                    *selection.errors,
                    *(
                        {**error.public(), "index": selection.indexes[error.index]}
                        for error in batch.errors
                    ),
                ],
                "total": selection.total,
                "next_offset": selection.next_offset,
                "forward_paths": selection.forward_paths,
            }
        )

    async def save_media(context: ToolContext, request: SaveMedia) -> ToolResult:
        if context.write_workspace is None:
            raise ValueError("沙箱文件写入服务不可用")
        selection = await media_segments(context, request)
        if len(selection.segments) != 1:
            raise ValueError("保存媒体必须通过 indexes 明确选择一个资源")
        asset = await reader.read(selection.segments[0], context.napcat)
        denied = context.recheck()
        if denied is not None:
            return denied
        # runner 的二进制写入以 O_EXCL 原子创建，不做先读取再覆盖的竞态检查。
        await context.write_workspace(request.destination, asset.content)
        return ToolResult.success(
            {"path": request.destination, "size": len(asset.content), "mime_type": asset.mime_type}
        )

    async def send(context: ToolContext, request: a.SendMessage) -> ToolResult:
        return await send_message(context, request, reader)

    async def add_request(context: ToolContext, request: a.GroupRequest) -> ToolResult:
        raw = await context.napcat.call("get_group_system_msg", {})
        filtered = filtered_groups(raw, context.target.peer_id)

        def contains(value: JsonValue) -> bool:
            if isinstance(value, list):
                return any(contains(item) for item in value)
            if isinstance(value, dict):
                if str(value.get("request_id", value.get("flag", ""))) == request.flag:
                    return True
                return any(contains(item) for item in value.values())
            return False

        if not contains(filtered):
            raise ValueError("无法确认该入群请求属于当前群")
        return await invoke(
            context,
            "set_group_add_request",
            request.model_dump(mode="json"),
        )

    custom: list[tuple[str, str, str, ToolBinding, str]] = [
        (
            "qq__get_history",
            "读取会话历史",
            "拉取当前会话的近期历史消息，支持向前翻页或按北京时间范围筛选。",
            model_binding(a.History, history),
            "messages",
        ),
        (
            "qq__get_message",
            "读取单条消息",
            "按 message_id 查询当前会话中的单条消息详情。",
            model_binding(a.MessageId, get_message),
            "messages",
        ),
        (
            "qq__get_forward_messages",
            "展开合并转发",
            "展开当前会话消息中的合并转发内容（需传入外层消息的 message_id）。",
            model_binding(a.Forward, get_forward),
            "messages",
        ),
        (
            "qq__read_media",
            "读取媒体内容",
            "读取消息或合并转发中的图片/语音/视频/文件、指定 QQ 号头像、当前群头像或 QQ 官方图床图片（message_id、user_id、group_avatar、media_url 四选一）。",
            model_binding(a.ReadMedia, read_media),
            "media",
        ),
        (
            "qq__send_message",
            "发送消息",
            "向 QQ 发送消息或文件的唯一入口。按 parts 顺序组合纯文本（不支持 Markdown 语法）、引用回复、@成员、表情、图片/语音/视频/文件、戳一戳、AI 语音（type=ai_voice）或合并转发。",
            model_binding(a.SendMessage, send),
            "messages",
        ),
    ]
    for name, title, description, binding, category in custom:
        tools.append(
            ToolDefinition(
                name,
                description,
                category,
                binding,
                cross_scope=True,
                title=title,
            )
        )
    tools.append(
        ToolDefinition(
            "qq__save_media_to_workspace",
            "将消息附件、QQ 头像、群头像或官方图床图片保存到沙箱工作区供代码处理（与 read_media 共用四选一来源，每次保存单个资源）。",
            "media",
            model_binding(SaveMedia, save_media),
            cross_scope=True,
            requires=("sandbox__write_file",),
            title="保存媒体到共享工作区",
        )
    )
    tools.append(
        ToolDefinition(
            "qq__set_group_add_request",
            "同意或拒绝当前群的入群申请与邀请。",
            "groups",
            model_binding(a.GroupRequest, add_request),
            scope="group",
            cross_scope=True,
            title="处理当前群入群申请",
            deferred=True,
        )
    )
    return tools


async def send_message(
    context: ToolContext, request: a.SendMessage, reader: MediaReader
) -> ToolResult:
    if any(
        isinstance(item, dict) and item.get("status") == "unknown" for item in context.sent_messages
    ):
        raise ValueError("本轮已有发送结果未确认，请勿重复发送")
    target = context.target
    destination: JsonObject = {"group_id" if target.kind == "group" else "user_id": target.peer_id}
    actions: list[tuple[str, JsonObject]] = []
    segments: list[JsonObject] = []
    total_media_size = 0

    def flush() -> None:
        if segments:
            actions.append(
                (
                    "send_group_msg" if target.kind == "group" else "send_private_msg",
                    {**destination, "message": list(segments)},
                )
            )
            segments.clear()

    for part in request.parts:
        if isinstance(part, a.TextPart):
            segments.append({"type": "text", "data": {"text": part.text}})
        elif isinstance(part, a.AtPart):
            if target.kind != "group":
                raise ValueError("私聊不支持艾特消息段")
            segments.append({"type": "at", "data": {"qq": part.qq}})
        elif isinstance(part, a.ReplyPart):
            await scoped_message(context.napcat, target, part.message_id)
            segments.append({"type": "reply", "data": {"id": part.message_id}})
        elif isinstance(part, a.FacePart):
            segments.append({"type": "face", "data": {"id": part.id}})
        elif isinstance(part, a.PokePart):
            flush()
            user_id = part.user_id or (target.peer_id if target.kind == "private" else "")
            if not user_id or (target.kind == "private" and user_id != target.peer_id):
                raise ValueError("戳一戳必须指定当前群成员或当前私聊对端")
            actions.append(
                (
                    "group_poke" if target.kind == "group" else "friend_poke",
                    {**destination, "user_id": user_id},
                )
            )
        elif isinstance(part, a.AIVoicePart):
            if target.kind != "group":
                raise ValueError("QQ AI 语音发送仅适用于群聊")
            flush()
            actions.append(
                (
                    "send_group_ai_record",
                    {**destination, "character": part.character, "text": part.text},
                )
            )
        elif isinstance(part, a.ForwardPart):
            flush()
            nodes: list[JsonValue] = []
            for message_id in part.message_ids:
                await scoped_message(context.napcat, context.scope, message_id)
                nodes.append({"type": "node", "data": {"id": message_id}})
            actions.append(
                (
                    "send_group_forward_msg"
                    if target.kind == "group"
                    else "send_private_forward_msg",
                    {**destination, "messages": nodes},
                )
            )
        else:
            if part.generated_id:
                generated = context.generated_images.get(part.generated_id)
                if generated is None:
                    raise ValueError("指定的 generated_id 不存在或已过期")
                content = generated.content
            elif part.workspace_path:
                if not context.read_workspace:
                    raise ValueError("共享工作区不可用")
                content = await context.read_workspace(part.workspace_path)
            else:
                content, _ = await reader.download(part.url or "")
            total_media_size += len(content)
            if total_media_size > reader.settings.media_max_bytes:
                raise ValueError("本次发送的媒体总量超过配置上限")
            encoded = "base64://" + base64.b64encode(content).decode("ascii")
            if part.type == "file":
                flush()
                actions.append(
                    (
                        "upload_group_file" if target.kind == "group" else "upload_private_file",
                        {**destination, "file": encoded, "name": part.name or "file"},
                    )
                )
            else:
                segments.append({"type": part.type, "data": {"file": encoded}})
    flush()
    if context.send_attempts + len(actions) > context.max_sends:
        raise ValueError("本轮实际发送次数将超过当前群限制；请合并正文或减少独立文件/转发")
    receipts: list[JsonValue] = []
    for action, params in actions:
        result = await invoke(context, action, params, count_send=True)
        receipt: JsonObject = {"action": action, "status": result.status, "data": result.data}
        receipts.append(receipt)
        if result.status in ("ok", "unknown"):
            context.sent_messages.append(receipt)
        if result.status != "ok":
            has_success = any(
                isinstance(item, dict) and item.get("status") == "ok" for item in receipts
            )
            return ToolResult(
                status=result.status,
                data={"receipts": receipts},
                error_type=result.error_type,
                message=result.message + (" 已成功的前序部分不要重发。" if has_success else ""),
            )
    return ToolResult.success({"receipts": receipts})
