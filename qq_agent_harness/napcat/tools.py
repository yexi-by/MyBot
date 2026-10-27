"""NapCat 能力目录与类型化内置工具。发送只有一个入口，读写来源由可信上下文绑定。"""

import base64
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from ..contracts import (
    JSON_OBJECT,
    JsonObject,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    schema,
)
from ..settings import NapCatSettings
from . import arguments as a
from .connection import (
    NapCatError,
    NapCatUnavailable,
    normalize_ids,
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
    "messages.read": "读取消息",
    "messages.send": "发送消息",
    "messages.manage": "管理消息",
    "media.read": "读取媒体",
    "members.read": "成员资料",
    "groups.read": "群资料",
    "groups.manage": "群管理",
    "notices.read": "读取群公告",
    "notices.manage": "管理群公告",
    "files.read": "读取群文件",
    "files.manage": "管理群文件",
    "albums.read": "浏览群相册",
    "albums.manage": "管理群相册",
    "contacts.read": "联系人资料",
    "contacts.manage": "管理联系人",
    "account.read": "机器人账号信息",
    "account.manage": "机器人账号设置",
    "utilities.read": "辅助查询",
    "system.manage": "NapCat 系统操作",
}


@dataclass(frozen=True)
class ActionSpec:
    action: str
    summary: str
    model: type[BaseModel]
    category: str
    scope: Literal["both", "group", "private", "account"] = "group"
    effects: bool = False
    message_bound: bool = False
    inject_peer: bool = True
    filter_groups: bool = False

    @property
    def name(self) -> str:
        return "qq__" + self.action.lstrip("_.")


# 只有明确列在目录中的业务动作可被调用；不能从模型输入选择原始 action。
ACTION_SPECS = (
    ActionSpec("get_login_info", "了解机器人账号身份", a.Empty, "account.read", "account"),
    ActionSpec("get_status", "确认 QQ 在线状态", a.Empty, "account.read", "account"),
    ActionSpec("get_robot_uin_range", "核实 QQ 机器人账号范围", a.Empty, "account.read", "account"),
    ActionSpec(
        "nc_get_packet_status", "核实扩展协议能力是否可用", a.Empty, "account.read", "account"
    ),
    ActionSpec(
        "get_version_info", "确认 NapCat 版本与接口可用性", a.Empty, "account.read", "account"
    ),
    ActionSpec("can_send_image", "检查图片发送能力", a.Empty, "account.read", "account"),
    ActionSpec("can_send_record", "检查语音发送能力", a.Empty, "account.read", "account"),
    ActionSpec("get_friend_list", "查看机器人好友列表", a.Fresh, "contacts.read", "account"),
    ActionSpec(
        "get_friends_with_category", "查看分组后的好友列表", a.Empty, "contacts.read", "account"
    ),
    ActionSpec(
        "get_unidirectional_friend_list", "查看单向好友信息", a.Empty, "contacts.read", "account"
    ),
    ActionSpec("get_recent_contact", "了解近期联系对象", a.Count, "contacts.read", "account"),
    ActionSpec("get_group_list", "了解机器人加入的群", a.Fresh, "account.read", "account"),
    ActionSpec(
        "get_group_info",
        "查询群名称、人数、容量和群主 QQ 号；群主缺失时按授权从成员列表补齐",
        a.Empty,
        "groups.read",
    ),
    ActionSpec(
        "get_group_info_ex",
        "读取群扩展协议标识；查群主或群介绍用 get_group_detail_info",
        a.Empty,
        "groups.read",
    ),
    ActionSpec(
        "get_group_detail_info",
        "查询群主 QQ 号、群介绍、加群问题和群规模，返回整理后的资料",
        a.Empty,
        "groups.read",
    ),
    ActionSpec("get_group_share_link", "取得当前群的分享链接", a.Empty, "groups.read"),
    ActionSpec(
        "get_group_member_list",
        "按身份、昵称、群名片、QQ 号或头衔查找成员，返回分页列表；已知 QQ 号用 get_group_member_info",
        a.GroupMemberList,
        "members.read",
    ),
    ActionSpec(
        "get_group_member_info",
        "查询指定成员的群名片、头衔、群主/管理员身份、入群与最后发言时间；可按授权附带 QQ 公开资料和在线状态",
        a.MemberInfo,
        "members.read",
    ),
    ActionSpec(
        "get_stranger_info",
        "查询 QQ 账号全局公开资料：昵称、签名 longNick、注册时间 regTime、等级 qqLevel、性别与地区；群名片和群身份用 get_group_member_info",
        a.UserFresh,
        "members.read",
        "account",
    ),
    ActionSpec("nc_get_user_status", "确认指定 QQ 账号在线状态", a.User, "members.read", "account"),
    ActionSpec("get_group_honor_info", "查看当前群荣誉信息", a.Honor, "groups.read"),
    ActionSpec("get_essence_msg_list", "查看当前群精华消息", a.Empty, "messages.read"),
    ActionSpec("get_group_at_all_remain", "检查当前群艾特全体的剩余次数", a.Empty, "groups.read"),
    ActionSpec("get_group_shut_list", "查看当前群禁言情况", a.Empty, "groups.read"),
    ActionSpec("get_group_signed_list", "查看当前群今日打卡成员", a.Empty, "groups.read"),
    ActionSpec("_get_group_notice", "查看当前群公告", a.Empty, "notices.read"),
    ActionSpec(
        "get_group_system_msg",
        "查看当前群的入群请求与邀请",
        a.Empty,
        "groups.read",
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "get_group_ignored_notifies",
        "查看当前群被忽略的通知记录；仅查被忽略的加群申请用 get_group_ignore_add_request",
        a.Empty,
        "groups.read",
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "get_group_ignore_add_request",
        "查看当前群被忽略的加群申请；一般入群请求与邀请用 get_group_system_msg",
        a.Empty,
        "groups.read",
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "set_group_ban", "管理当前群成员的禁言状态", a.MemberBan, "groups.manage", effects=True
    ),
    ActionSpec(
        "set_group_whole_ban", "调整当前群全员禁言", a.Enabled, "groups.manage", effects=True
    ),
    ActionSpec("set_group_kick", "移出当前群成员", a.MemberKick, "groups.manage", effects=True),
    ActionSpec(
        "set_group_kick_members", "批量移出当前群成员", a.MembersKick, "groups.manage", effects=True
    ),
    ActionSpec(
        "set_group_admin",
        "调整当前群的 QQ 平台管理员",
        a.MemberAdmin,
        "groups.manage",
        effects=True,
    ),
    ActionSpec("set_group_card", "修改当前群成员名片", a.MemberCard, "groups.manage", effects=True),
    ActionSpec(
        "set_group_special_title",
        "修改当前群成员头衔",
        a.MemberTitle,
        "groups.manage",
        effects=True,
    ),
    ActionSpec("set_group_name", "修改当前群名称", a.GroupName, "groups.manage", effects=True),
    ActionSpec(
        "set_group_remark",
        "修改当前群在机器人账号中的备注",
        a.GroupRemark,
        "groups.manage",
        effects=True,
    ),
    ActionSpec("set_group_leave", "退出或解散当前群", a.GroupLeave, "groups.manage", effects=True),
    ActionSpec("set_group_sign", "在当前群打卡", a.Empty, "groups.manage", effects=True),
    ActionSpec(
        "_send_group_notice", "发布当前群公告", a.GroupNotice, "notices.manage", effects=True
    ),
    ActionSpec(
        "_del_group_notice", "删除当前群指定公告", a.NoticeId, "notices.manage", effects=True
    ),
    ActionSpec(
        "set_essence_msg",
        "将当前群消息设为精华；取消使用 delete_essence_msg",
        a.MessageId,
        "messages.manage",
        effects=True,
        message_bound=True,
        inject_peer=False,
    ),
    ActionSpec(
        "delete_essence_msg",
        "移除当前群消息的精华标记；添加使用 set_essence_msg",
        a.MessageId,
        "messages.manage",
        effects=True,
        message_bound=True,
        inject_peer=False,
    ),
    ActionSpec(
        "delete_msg",
        "撤回当前会话中的指定消息",
        a.MessageId,
        "messages.manage",
        "both",
        True,
        True,
        False,
    ),
    ActionSpec(
        "set_msg_emoji_like",
        "添加或取消消息的指定表情回应：set=true 添加，set=false 取消",
        a.Reaction,
        "messages.manage",
        "both",
        True,
        True,
        False,
    ),
    ActionSpec(
        "mark_msg_as_read",
        "标记当前会话的指定消息为已读",
        a.MessageId,
        "messages.manage",
        "both",
        True,
        True,
        False,
    ),
    ActionSpec("get_group_file_system_info", "了解当前群文件空间", a.Empty, "files.read"),
    ActionSpec("get_group_root_files", "浏览当前群根目录文件", a.Empty, "files.read"),
    ActionSpec("get_group_files_by_folder", "浏览当前群文件夹", a.Folder, "files.read"),
    ActionSpec("get_group_file_url", "取得当前群文件下载地址", a.FileId, "files.read"),
    ActionSpec(
        "create_group_file_folder", "创建当前群文件夹", a.FolderCreate, "files.manage", effects=True
    ),
    ActionSpec(
        "delete_group_file", "删除当前群指定文件", a.GroupFile, "files.manage", effects=True
    ),
    ActionSpec(
        "delete_group_folder", "删除当前群指定文件夹", a.Folder, "files.manage", effects=True
    ),
    ActionSpec("move_group_file", "移动当前群文件", a.MoveFile, "files.manage", effects=True),
    ActionSpec("rename_group_file", "重命名当前群文件", a.RenameFile, "files.manage", effects=True),
    ActionSpec("send_like", "给 QQ 资料点赞", a.Like, "contacts.manage", "account", True),
    ActionSpec(
        "set_friend_remark", "修改好友备注", a.FriendRemark, "contacts.manage", "account", True
    ),
    ActionSpec("delete_friend", "删除指定好友关系", a.User, "contacts.manage", "account", True),
    ActionSpec(
        "set_friend_add_request",
        "处理好友申请",
        a.FriendRequest,
        "contacts.manage",
        "account",
        True,
    ),
    ActionSpec(
        "get_doubt_friends_add_request", "查看可疑好友申请", a.Empty, "contacts.read", "account"
    ),
    ActionSpec("get_online_clients", "查看账号的在线客户端", a.Empty, "account.read", "account"),
    ActionSpec(
        "set_self_longnick", "修改机器人个性签名", a.LongNick, "account.manage", "account", True
    ),
    ActionSpec(
        "set_online_status", "修改机器人在线状态", a.OnlineStatus, "account.manage", "account", True
    ),
    ActionSpec(
        "set_diy_online_status",
        "修改机器人自定义在线状态",
        a.DiyStatus,
        "account.manage",
        "account",
        True,
    ),
    ActionSpec(
        "set_qq_profile", "修改机器人公开资料", a.QQProfile, "account.manage", "account", True
    ),
    ActionSpec(
        "fetch_custom_face",
        "列出收藏表情的图片 URL，可用 read_media 的 media_url 查看；删除前先用 fetch_custom_face_detail 获取 resId",
        a.Count,
        "account.read",
        "account",
    ),
    ActionSpec(
        "get_collection_list", "查看机器人收藏内容", a.Collection, "account.read", "account"
    ),
    ActionSpec("get_profile_like", "查看机器人资料点赞情况", a.Signed, "account.read", "account"),
    ActionSpec(
        "get_ai_characters",
        "查询群 AI 语音角色 character ID；发送时直接在 send_message.parts 中使用 type=ai_voice、character、text",
        a.AICharacters,
        "media.read",
    ),
    ActionSpec(
        "get_ai_record",
        "生成 QQ AI 语音资源供读取处理；只需发送时直接用 send_message.parts 的 ai_voice，无需先生成资源",
        a.AIRecord,
        "media.read",
    ),
    ActionSpec("get_qun_album_list", "浏览当前群相册", a.Empty, "albums.read"),
    ActionSpec("get_group_album_media_list", "浏览当前群相册中的媒体", a.AlbumMedia, "albums.read"),
    ActionSpec("translate_en2zh", "翻译不熟悉的英文词句", a.Words, "utilities.read", "account"),
    ActionSpec(
        "check_url_safely", "核对 QQ 对公网链接的风险判断", a.CheckUrl, "utilities.read", "account"
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
    effects: bool = False,
    count_send: bool = False,
) -> ToolResult:
    if context.cancelled():
        return ToolResult(status="cancelled", message="当前轮次已取消，动作未提交")
    denied = context.recheck()
    if denied is not None:
        return denied
    if count_send:
        context.send_attempts += 1
    context.effect_submitted = effects
    try:
        data = await context.napcat.call(action, params)
        return ToolResult.success(normalize_ids(data))
    except NapCatError as exc:
        if effects and re.search(
            r"Timeout: NTEvent\s+serviceAndMethod:NodeIKernelMsgService/sendMsg\s+"
            r"ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate\b",
            str(exc),
        ):
            # 上游已调用 sendMsg，只是没等到匹配的成功通知，不能断言未送达。
            return ToolResult(
                status="unknown",
                error_type=f"NapCat:{exc.code}",
                message="QQ 发送接口已调用，但等待发送成功回执超时；是否送达尚未确认，核实前不要重发。",
            )
        return ToolResult.failure(str(exc), f"NapCat:{exc.code}")
    except NapCatUnavailable as exc:
        if not exc.submitted:
            context.effect_submitted = False
            if count_send:
                context.send_attempts -= 1
        return ToolResult(
            status="unknown" if effects and exc.submitted else "error",
            error_type="NapCatUnavailable",
            message=str(exc),
        )
    except (TimeoutError, ConnectionError) as exc:
        return ToolResult(
            status="unknown" if effects else "error",
            error_type=type(exc).__name__,
            message="动作已提交但没有取得确定回执；请先核实，不能直接重试。"
            if effects
            else "NapCat 连接失败或读取超时",
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
        JSON_OBJECT.validate_python(request.model_dump()),
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
    for include, action, field, args in (
        (
            request.include_profile,
            "get_stranger_info",
            "profile",
            {"user_id": request.user_id, "no_cache": request.no_cache},
        ),
        (request.include_status, "nc_get_user_status", "status", {"user_id": request.user_id}),
    ):
        if not include:
            continue
        params = JSON_OBJECT.validate_python(args)
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
    async def handle(context: ToolContext, arguments: JsonObject) -> ToolResult:
        params = JSON_OBJECT.validate_python(
            spec.model.model_validate(arguments).model_dump(exclude_none=True)
        )
        if spec.action == "get_group_member_list":
            params = {"no_cache": params["no_cache"]}
        elif spec.action == "get_group_member_info":
            params = {"user_id": params["user_id"], "no_cache": params["no_cache"]}
        if spec.message_bound:
            await scoped_message(context.napcat, context.target, str(params["message_id"]))
        if spec.inject_peer:
            if spec.scope == "group":
                params["group_id"] = context.target.peer_id
            elif spec.scope == "private":
                params["user_id"] = context.target.peer_id
        result = await invoke(context, spec.action, params, effects=spec.effects)
        if result.status == "ok" and spec.filter_groups:
            result.data = filtered_groups(result.data, context.target.peer_id)
        if result.status != "ok":
            return result
        if spec.action == "get_group_member_list":
            result.data = member_page(result.data, a.GroupMemberList.model_validate(arguments))
        elif spec.action in ("get_group_info", "get_group_detail_info"):
            result.data = group_summary(result.data, context.target.peer_id)
            interrupted = await complete_group_owner(context, result.data)
            if interrupted:
                return interrupted
            avatar: JsonObject = {"tool": "qq__read_media", "group_avatar": True}
            if context.target != context.scope:
                avatar["target_group_id"] = context.target.peer_id
            result.data["avatar"] = avatar
        elif spec.action == "get_group_member_info":
            return await complete_member(context, result, a.MemberInfo.model_validate(arguments))
        return result

    return ToolDefinition(
        spec.name,
        spec.summary,
        spec.category,
        schema(spec.model),
        handle,
        effects=spec.effects,
        scope=spec.scope,
        cross_scope=spec.scope in ("both", "group", "private"),
        model=spec.model,
        title=ACTION_TITLES.get(spec.action, spec.summary),
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
        "将机器人账号所有会话标记已读",
        a.Empty,
        "account.manage",
        "account",
        True,
    ),
    ActionSpec(
        "set_restart",
        "重启 NapCat 服务；将中断所有 QQ 会话连接",
        a.Empty,
        "system.manage",
        "account",
        True,
    ),
    ActionSpec(
        "bot_exit",
        "退出 NapCat 进程；将中断所有 QQ 会话连接",
        a.Empty,
        "system.manage",
        "account",
        True,
    ),
    ActionSpec(
        "clean_cache",
        "清除 NapCat 和 QQ 媒体缓存；可能使旧媒体不可再次读取",
        a.Empty,
        "system.manage",
        "account",
        True,
    ),
    ActionSpec(
        "fetch_custom_face_detail",
        "查询收藏表情的 URL 与 resId；删除时把 resId 传给 delete_custom_face.res_id",
        a.Count,
        "account.read",
        "account",
    ),
    ActionSpec(
        "delete_custom_face",
        "删除收藏表情；res_id 必须使用 fetch_custom_face_detail 返回的 resId",
        a.DeleteFaces,
        "account.manage",
        "account",
        True,
    ),
    ActionSpec(
        "get_emoji_likes",
        "按已知 emoji_id 读取群消息点亮该表情的用户列表；群聊/私聊分页用 fetch_emoji_like",
        a.ReactionList,
        "messages.read",
        message_bound=True,
    ),
    ActionSpec(
        "fetch_emoji_like",
        "按已知 emojiId 分页查询群聊或私聊消息点亮该表情的用户列表；用返回的 cookie 续页，不枚举所有表情",
        a.EmojiPage,
        "messages.read",
        "both",
        False,
        True,
        False,
    ),
    ActionSpec(
        "create_collection",
        "保存内容到 QQ 收藏",
        a.CreateCollection,
        "account.manage",
        "account",
        True,
    ),
    ActionSpec(
        "set_doubt_friends_add_request",
        "接受可疑好友申请",
        a.DoubtRequest,
        "contacts.manage",
        "account",
        True,
    ),
    ActionSpec(
        "set_input_status",
        "在当前私聊展示输入状态",
        a.InputStatus,
        "messages.manage",
        "private",
        True,
    ),
    ActionSpec(
        "set_group_todo",
        "将群消息设为 QQ 群待办；完成用 complete_group_todo，取消用 cancel_group_todo",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "complete_group_todo",
        "完成群消息对应的 QQ 群待办；创建用 set_group_todo，取消用 cancel_group_todo",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "cancel_group_todo",
        "取消群消息对应的 QQ 群待办；创建用 set_group_todo，标为完成用 complete_group_todo",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "set_group_album_media_like",
        "点赞群相册媒体；取消使用 cancel_group_album_media_like",
        a.AlbumLike,
        "albums.manage",
        effects=True,
    ),
    ActionSpec(
        "cancel_group_album_media_like",
        "取消群相册媒体点赞；添加使用 set_group_album_media_like",
        a.AlbumLike,
        "albums.manage",
        effects=True,
    ),
    ActionSpec(
        "del_group_album_media", "删除当前群相册媒体", a.AlbumDelete, "albums.manage", effects=True
    ),
    ActionSpec(
        "do_group_album_comment",
        "评论当前群相册媒体",
        a.AlbumComment,
        "albums.manage",
        effects=True,
    ),
    ActionSpec(
        "set_group_add_option", "设置当前群入群条件", GroupAddOption, "groups.manage", effects=True
    ),
    ActionSpec(
        "set_group_member_invite_policy",
        "设置当前群的成员邀请规则",
        InvitePolicy,
        "groups.manage",
        effects=True,
    ),
    ActionSpec(
        "set_group_member_permissions",
        "设置 QQ 平台群功能开关",
        MemberPermissions,
        "groups.manage",
        effects=True,
    ),
    ActionSpec(
        "set_group_new_member_history_visibility",
        "设置 QQ 平台新成员历史可见性",
        HistoryVisibility,
        "groups.manage",
        effects=True,
    ),
)


def build_tools(settings: NapCatSettings | None = None) -> list[ToolDefinition]:
    reader = MediaReader(settings)
    tools = [action_tool(spec) for spec in (*ACTION_SPECS, *EXTRA_SPECS)]

    def avatar_tool(group: bool) -> ToolDefinition:
        action = "set_group_portrait" if group else "set_qq_avatar"

        async def avatar(context: ToolContext, arguments: JsonObject) -> ToolResult:
            source = a.ImageSource.model_validate(arguments)
            if source.workspace_path:
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
            return await invoke(context, action, params, effects=True)

        return ToolDefinition(
            "qq__" + action,
            "当你判断有必要更新当前群或机器人头像时，主动调用。图片从获准的共享工作区或公网读取，不接受宿主机路径。",
            "groups.manage" if group else "account.manage",
            schema(a.ImageSource),
            avatar,
            effects=True,
            scope="group" if group else "account",
            cross_scope=group,
            model=a.ImageSource,
            title="更新当前群头像" if group else "更新机器人头像",
        )

    tools.extend((avatar_tool(True), avatar_tool(False)))

    async def history(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.History.model_validate(arguments)
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
                "coverage": "仅包含 NapCat 本次返回的有界历史；不能据此认定此前没有消息",
            }
        )

    async def get_message(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.MessageId.model_validate(arguments)
        return ToolResult.success(
            await scoped_message(context.napcat, context.target, request.message_id)
        )

    async def get_forward(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.Forward.model_validate(arguments)
        return ToolResult.success(
            await forward_bundle(
                context,
                request.message_id,
                forward_path=request.forward_path,
                offset=request.offset,
                limit=request.limit,
            )
        )

    async def read_media(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.ReadMedia.model_validate(arguments)
        selection = await media_segments(context, request)
        result = await reader.read_many(selection.segments, context.napcat)
        for field in ("media", "errors"):
            entries = result.get(field)
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and isinstance(index := entry.get("index"), int):
                    entry["index"] = selection.indexes[index]
        media_errors = result.get("errors")
        result["errors"] = [
            *selection.errors,
            *(media_errors if isinstance(media_errors, list) else []),
        ]
        result.update(
            total=selection.total,
            next_offset=selection.next_offset,
            forward_paths=selection.forward_paths,
        )
        return ToolResult.success(result)

    async def save_media(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = SaveMedia.model_validate(arguments)
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
        context.effect_submitted = True
        await context.write_workspace(request.destination, asset.content)
        return ToolResult.success(
            {"path": request.destination, "size": len(asset.content), "mime_type": asset.mime_type}
        )

    async def send(context: ToolContext, arguments: JsonObject) -> ToolResult:
        return await send_message(context, a.SendMessage.model_validate(arguments), reader)

    async def add_request(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.GroupRequest.model_validate(arguments)
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
            JSON_OBJECT.validate_python(request.model_dump()),
            effects=True,
        )

    custom: list[
        tuple[
            str,
            str,
            str,
            type[BaseModel],
            str,
            Callable[[ToolContext, JsonObject], Awaitable[ToolResult]],
            bool,
        ]
    ] = [
        (
            "qq__get_history",
            "读取会话历史",
            "主动补充当前会话上下文、核对前文或按北京时间筛选近期消息。远端历史不是完整归档。",
            a.History,
            "messages.read",
            history,
            False,
        ),
        (
            "qq__get_message",
            "读取单条消息",
            "当你需要核实当前会话的引用、消息原文或消息段时，主动读取这条消息；不会读取其他会话。",
            a.MessageId,
            "messages.read",
            get_message,
            False,
        ),
        (
            "qq__get_forward_messages",
            "展开合并转发",
            "当对话提及合并转发但缺少内容时，主动展开当前会话消息中的转发节点。必须提供外层消息 ID。",
            a.Forward,
            "messages.read",
            get_forward,
            False,
        ),
        (
            "qq__read_media",
            "读取媒体内容",
            "读取消息或转发中的媒体、指定 QQ 用户头像、当前群头像或 QQ 官方图床图片。message_id、user_id、group_avatar、media_url 四选一；单个资源失败返回部分错误。",
            a.ReadMedia,
            "media.read",
            read_media,
            False,
        ),
        (
            "qq__send_message",
            "发送消息",
            "在 QQ 发言或交付文件的唯一入口，按 parts 顺序发送正文、引用、艾特、媒体；AI 语音直接用 type=ai_voice、character、text。普通模型正文不会发送，结果未知时不得重发。",
            a.SendMessage,
            "messages.send",
            send,
            True,
        ),
    ]
    for name, title, description, model, category, handler, effects in custom:
        tools.append(
            ToolDefinition(
                name,
                description,
                category,
                schema(model),
                handler,
                effects=effects,
                cross_scope=True,
                model=model,
                title=title,
            )
        )
    tools.append(
        ToolDefinition(
            "qq__save_media_to_workspace",
            "将消息媒体、QQ 用户头像、群头像或 QQ 官方图床图片保存到共享工作区，用于代码分析和转换。与 read_media 共用四选一来源；指定一个资源和相对文件名，不覆盖已有文件。",
            "media.read",
            schema(SaveMedia),
            save_media,
            effects=True,
            cross_scope=True,
            model=SaveMedia,
            requires=("sandbox__write_file",),
            title="保存媒体到共享工作区",
        )
    )
    tools.append(
        ToolDefinition(
            "qq__set_group_add_request",
            "当你判断有必要处理当前群入群申请时，主动调用；申请必须能从当前群请求列表验证归属。",
            "groups.manage",
            schema(a.GroupRequest),
            add_request,
            effects=True,
            scope="group",
            cross_scope=True,
            model=a.GroupRequest,
            title="处理当前群入群申请",
        )
    )
    return tools


async def send_message(
    context: ToolContext, request: a.SendMessage, reader: MediaReader
) -> ToolResult:
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
            if part.workspace_path:
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
        result = await invoke(context, action, params, effects=True, count_send=True)
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
