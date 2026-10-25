"""NapCat 能力目录与类型化内置工具。发送只有一个入口，读写来源由可信上下文绑定。"""

import base64
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

BASELINE_VERSION = "v4.18.41"
BASELINE_COMMIT = "859eb530c5801577633c2c3ae871d23586edf281"
BEIJING = timezone(timedelta(hours=8))


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
    ActionSpec("get_group_info", "了解当前群名称与规模", a.Empty, "groups.read"),
    ActionSpec("get_group_info_ex", "了解当前群的扩展资料", a.Empty, "groups.read"),
    ActionSpec("get_group_detail_info", "查看当前群详细资料", a.Empty, "groups.read"),
    ActionSpec("get_group_share_link", "取得当前群的分享链接", a.Empty, "groups.read"),
    ActionSpec("get_group_member_list", "了解当前群成员", a.Fresh, "members.read"),
    ActionSpec("get_group_member_info", "核实当前群某位成员的资料", a.UserFresh, "members.read"),
    ActionSpec(
        "get_stranger_info", "核实指定 QQ 账号的公开资料", a.UserFresh, "members.read", "account"
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
        "查看当前群被忽略的通知",
        a.Empty,
        "groups.read",
        inject_peer=False,
        filter_groups=True,
    ),
    ActionSpec(
        "get_group_ignore_add_request",
        "查看当前群被忽略的加群请求",
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
        "将当前群消息设为精华",
        a.MessageId,
        "messages.manage",
        effects=True,
        message_bound=True,
        inject_peer=False,
    ),
    ActionSpec(
        "delete_essence_msg",
        "移除当前群消息的精华标记",
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
        "对当前会话消息作表情回应",
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
    ActionSpec("fetch_custom_face", "查找机器人收藏的表情图片", a.Count, "account.read", "account"),
    ActionSpec(
        "get_collection_list", "查看机器人收藏内容", a.Collection, "account.read", "account"
    ),
    ActionSpec("get_profile_like", "查看机器人资料点赞情况", a.Signed, "account.read", "account"),
    ActionSpec("get_ai_characters", "查看当前群可用 AI 语音角色", a.AICharacters, "media.read"),
    ActionSpec("get_ai_record", "为当前群生成 QQ AI 语音资源", a.AIRecord, "media.read"),
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
    context: ToolContext, action: str, params: JsonObject, *, effects: bool = False
) -> ToolResult:
    if context.cancelled():
        return ToolResult(status="cancelled", message="当前轮次已取消，动作未提交")
    denied = context.recheck()
    if denied is not None:
        return denied
    try:
        data = await context.napcat.call(action, params)
        return ToolResult.success(normalize_ids(data))
    except NapCatError as exc:
        return ToolResult.failure(str(exc), f"NapCat:{exc.code}")
    except NapCatUnavailable as exc:
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


def action_tool(spec: ActionSpec) -> ToolDefinition:
    async def handle(context: ToolContext, arguments: JsonObject) -> ToolResult:
        params = JSON_OBJECT.validate_python(
            spec.model.model_validate(arguments).model_dump(exclude_none=True)
        )
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
        return result

    return ToolDefinition(
        spec.name,
        f"当你需要{spec.summary}来理解对话或完成你判断有必要的行动时，主动调用此工具。典型场景包括对话提及相关对象而你缺少资料，或当前群策略允许且有必要调整对应状态。作用范围由当前会话及已授权目标绑定。",
        spec.category,
        schema(spec.model),
        handle,
        effects=spec.effects,
        scope=spec.scope,
        cross_scope=spec.scope in ("both", "group", "private"),
        model=spec.model,
    )


async def forward_bundle(context: ToolContext, message_id: str) -> JsonObject:
    message = await scoped_message(context.napcat, context.target, message_id)
    segments = message.get("message", [])
    if not isinstance(segments, list):
        return {"messages": [], "errors": []}
    errors: list[JsonValue] = []
    calls = 0

    async def expand(value: JsonValue, depth: int, ancestry: frozenset[str]) -> JsonValue:
        if depth > 24:
            errors.append(
                {
                    "error_type": "ForwardDepthLimit",
                    "message": "嵌套转发层级超过限制，部分内容未展开",
                }
            )
            return None
        if isinstance(value, list):
            if len(value) > 200:
                errors.append(
                    {
                        "error_type": "ForwardCountLimit",
                        "message": "转发节点超过限制，仅展开前 200 项",
                    }
                )
            return [await expand(item, depth + 1, ancestry) for item in value[:200]]
        if isinstance(value, dict):
            result = dict(value)
            data = value.get("data")
            if value.get("type") == "forward" and isinstance(data, dict) and data.get("id"):
                forward_id = str(data["id"])
                result["data"] = {**data, "messages": await load(forward_id, depth + 1, ancestry)}
            else:
                for key in ("message", "content", "data"):
                    if key in result:
                        result[key] = await expand(result[key], depth + 1, ancestry)
            return result
        return value

    async def load(forward_id: str, depth: int, ancestry: frozenset[str]) -> JsonValue:
        nonlocal calls
        if forward_id in ancestry or calls >= 20 or depth > 24:
            errors.append(
                {
                    "forward_id": forward_id,
                    "error_type": "ForwardLimit",
                    "message": "嵌套转发出现循环或超过展开限额",
                }
            )
            return []
        calls += 1
        try:
            data = object_value(
                await context.napcat.call("get_forward_msg", {"message_id": forward_id})
            )
            return await expand(data.get("messages", []), depth + 1, ancestry | {forward_id})
        except (NapCatError, TimeoutError, ConnectionError) as exc:
            errors.append(
                {
                    "forward_id": forward_id,
                    "error_type": type(exc).__name__,
                    "message": "部分转发内容无法读取",
                }
            )
            return []

    nodes: list[JsonValue] = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("type") != "forward":
            continue
        data = segment.get("data")
        if isinstance(data, dict) and data.get("id"):
            values = await load(str(data["id"]), 0, frozenset())
            if isinstance(values, list):
                nodes.extend(values)
    return {"messages": nodes, "errors": errors}


async def media_segments(
    context: ToolContext, request: a.ReadMedia
) -> tuple[list[JsonObject], list[JsonValue]]:
    message = await scoped_message(context.napcat, context.target, request.message_id)
    raw = message.get("message", [])
    result: list[JsonObject] = []
    errors: list[JsonValue] = []

    def collect(value: JsonValue, depth: int = 0) -> None:
        if depth > 30 or len(result) >= 20:
            return
        if isinstance(value, list):
            for item in value:
                collect(item, depth + 1)
        elif isinstance(value, dict):
            if value.get("type") in ("image", "record", "video", "file"):
                result.append(value)
            elif "message" in value:
                collect(value["message"], depth + 1)
            elif "content" in value:
                collect(value["content"], depth + 1)
            elif "messages" in value:
                collect(value["messages"], depth + 1)
            elif value.get("type") in ("node", "forward"):
                collect(value.get("data"), depth + 1)

    collect(raw)
    if request.include_forwards:
        bundle = await forward_bundle(context, request.message_id)
        collect(bundle.get("messages"))
        failures = bundle.get("errors")
        if isinstance(failures, list):
            errors.extend(failures)
    if request.indexes is not None:
        if any(index < 0 or index >= len(result) for index in request.indexes):
            raise ValueError("媒体索引超出当前消息可读取范围")
        return [result[index] for index in request.indexes], errors
    return result, errors


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
        "fetch_custom_face_detail", "查看收藏表情的原生资源信息", a.Count, "account.read", "account"
    ),
    ActionSpec(
        "delete_custom_face",
        "删除机器人收藏的指定表情",
        a.DeleteFaces,
        "account.manage",
        "account",
        True,
    ),
    ActionSpec(
        "get_emoji_likes",
        "查看当前群消息的表情回应",
        a.ReactionList,
        "messages.read",
        message_bound=True,
    ),
    ActionSpec(
        "fetch_emoji_like",
        "分页查看当前会话消息的表情回应",
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
        "将当前群消息设置为 QQ 群待办",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "complete_group_todo",
        "完成当前群消息对应的 QQ 群待办",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "cancel_group_todo",
        "取消当前群消息对应的 QQ 群待办",
        a.GroupTodo,
        "groups.manage",
        effects=True,
        message_bound=True,
    ),
    ActionSpec(
        "set_group_album_media_like",
        "点赞当前群相册媒体",
        a.AlbumLike,
        "albums.manage",
        effects=True,
    ),
    ActionSpec(
        "cancel_group_album_media_like",
        "取消当前群相册媒体点赞",
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
        return ToolResult.success(await forward_bundle(context, request.message_id))

    async def read_media(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = a.ReadMedia.model_validate(arguments)
        segments, errors = await media_segments(context, request)
        result = await reader.read_many(segments, context.napcat)
        media_errors = result.get("errors")
        result["errors"] = [*errors, *(media_errors if isinstance(media_errors, list) else [])]
        return ToolResult.success(result)

    async def save_media(context: ToolContext, arguments: JsonObject) -> ToolResult:
        request = SaveMedia.model_validate(arguments)
        if context.write_workspace is None:
            raise ValueError("沙箱文件写入服务不可用")
        segments, _ = await media_segments(context, request)
        if len(segments) != 1:
            raise ValueError("保存媒体必须通过 indexes 明确选择一个资源")
        asset = await reader.read(segments[0], context.napcat)
        denied = context.recheck()
        if denied is not None:
            return denied
        # runner 的二进制写入以 O_EXCL 原子创建，不做先读取再覆盖的竞态检查。
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
            type[BaseModel],
            str,
            Callable[[ToolContext, JsonObject], Awaitable[ToolResult]],
            bool,
        ]
    ] = [
        (
            "qq__get_history",
            "主动补充当前会话上下文、核对前文或按北京时间筛选近期消息。远端历史不是完整归档。",
            a.History,
            "messages.read",
            history,
            False,
        ),
        (
            "qq__get_message",
            "当你需要核实当前会话的引用、消息原文或消息段时，主动读取这条消息；不会读取其他会话。",
            a.MessageId,
            "messages.read",
            get_message,
            False,
        ),
        (
            "qq__get_forward_messages",
            "当对话提及合并转发但缺少内容时，主动展开当前会话消息中的转发节点。必须提供外层消息 ID。",
            a.Forward,
            "messages.read",
            get_forward,
            False,
        ),
        (
            "qq__read_media",
            "当你需要理解当前消息或转发中的图片、语音、视频和文件时，主动读取其实际内容。单个资源失败返回部分错误。",
            a.ReadMedia,
            "media.read",
            read_media,
            False,
        ),
        (
            "qq__send_message",
            "当你判断有必要在 QQ 发言或交付文件时，主动调用此唯一发送入口。明确安排正文、引用、艾特及媒体顺序；普通模型正文不会发送，结果未知时不得重发。",
            a.SendMessage,
            "messages.send",
            send,
            True,
        ),
    ]
    for name, description, model, category, handler, effects in custom:
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
            )
        )
    tools.append(
        ToolDefinition(
            "qq__save_media_to_workspace",
            "当你需要用沙箱代码分析或转换 QQ 媒体时，主动将当前会话中的一个媒体保存到共享工作区；先选定资源和文件名，避免覆盖无关成果。",
            "media.read",
            schema(SaveMedia),
            save_media,
            effects=True,
            cross_scope=True,
            model=SaveMedia,
            requires=("sandbox__write_file",),
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
        )
    )
    return tools


async def send_message(
    context: ToolContext, request: a.SendMessage, reader: MediaReader
) -> ToolResult:
    if any(item.get("status") == "unknown" for item in context.sent_messages):
        return ToolResult.failure(
            "当前轮次有发送结果未知，不能继续重复派发；请先核实结果", "UncertainSend"
        )
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
    if len(context.sent_messages) + len(actions) > context.max_sends:
        raise ValueError("本轮实际发送次数将超过当前群限制；请合并正文或减少独立文件/转发")
    receipts: list[JsonValue] = []
    for action, params in actions:
        result = await invoke(context, action, params, effects=True)
        receipt: JsonObject = {"action": action, "status": result.status, "data": result.data}
        receipts.append(receipt)
        if result.status in ("ok", "unknown"):
            context.sent_messages.append(receipt)
        if result.status != "ok":
            return ToolResult(
                status=result.status,
                data={"receipts": receipts},
                error_type=result.error_type,
                message=result.message + " 已成功的前序部分不要重发。",
            )
    return ToolResult.success({"receipts": receipts})
