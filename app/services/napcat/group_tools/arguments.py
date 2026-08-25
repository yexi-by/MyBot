"""NapCat 群聊工具参数模型。"""

from datetime import timedelta, timezone
from typing import Literal

from pydantic import Field, model_validator

from app.models import StrictModel

MENTION_ALL: Literal["all"] = "all"
BEIJING_TIMEZONE: timezone = timezone(timedelta(hours=8))
HISTORY_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
type HistoryQueryMode = Literal[
    "recent_count", "recent_duration", "date_range", "around_message"
]
type ForwardImageQueryMode = Literal["single", "message", "all"]


class ListGroupRootFilesArgs(StrictModel):
    """获取当前群根目录文件列表的工具参数。"""

    file_count: int | None = Field(
        default=None,
        ge=1,
        description="需要返回的文件数量；为空时使用插件配置。",
    )


class ListGroupFilesByFolderArgs(StrictModel):
    """获取当前群指定文件夹文件列表的工具参数。"""

    folder_id: str | None = Field(
        default=None,
        description="群文件夹 ID。若已知 folder_id，优先填写此字段。",
    )
    folder: str | None = Field(
        default=None,
        description="群文件夹路径或名称。仅在没有 folder_id 时填写。",
    )
    file_count: int | None = Field(
        default=None,
        ge=1,
        description="需要返回的文件数量；为空时使用插件配置。",
    )

    @model_validator(mode="after")
    def check_folder_target(self) -> "ListGroupFilesByFolderArgs":
        """确保模型明确指定要查看的群文件夹。"""
        if self.folder_id is None and self.folder is None:
            raise ValueError("folder_id 和 folder 至少填写一个")
        return self


class GetGroupFileUrlArgs(StrictModel):
    """获取当前群指定文件下载链接的工具参数。"""

    file_id: str = Field(description="群文件 ID。通常先查询群文件列表后再填写。")


class GetForwardMessageArgs(StrictModel):
    """获取合并转发消息详情的工具参数。"""

    message_id: str = Field(
        description=(
            "当前群中外层群消息的消息 ID；该消息必须未撤回，"
            "并且恰好包含一个顶层合并转发段。"
        )
    )


class GetForwardMessageImagesArgs(StrictModel):
    """获取合并转发图片的工具参数。"""

    message_id: str = Field(
        description=(
            "当前群中外层群消息的消息 ID；该消息必须未撤回，"
            "并且恰好包含一个顶层合并转发段。"
        )
    )
    mode: ForwardImageQueryMode = Field(
        default="single",
        description=(
            "图片选择模式。single 表示读取单张图片；message 表示读取某条消息内全部图片；"
            "all 表示读取整个合并转发内的图片。"
        ),
    )
    message_index: int | None = Field(
        default=None,
        ge=1,
        description="single/message 模式使用：合并转发内第几条消息，从 1 开始。",
    )
    image_index: int | None = Field(
        default=None,
        ge=1,
        description="single 模式使用：目标消息内第几张图片，从 1 开始。",
    )
    max_images: int | None = Field(
        default=None,
        ge=1,
        description="本次最多读取的图片数量；为空时使用插件配置上限。",
    )

    @model_validator(mode="after")
    def check_image_query_arguments(self) -> "GetForwardMessageImagesArgs":
        """校验图片选择模式和定位参数的一致性。"""
        if self.mode == "single":
            if self.message_index is None or self.image_index is None:
                raise ValueError("single 模式必须填写 message_index 和 image_index")
            return self
        if self.mode == "message":
            if self.message_index is None:
                raise ValueError("message 模式必须填写 message_index")
            if self.image_index is not None:
                raise ValueError("message 模式不能填写 image_index")
            return self
        if self.message_index is not None or self.image_index is not None:
            raise ValueError("all 模式不能填写 message_index 或 image_index")
        return self


class HistoryCursorArgs(StrictModel):
    """群历史稳定分页游标。"""

    occurred_at: str = Field(
        description="上一页最后一条消息的 ISO 8601 时间，由工具结果原样返回。"
    )
    row_id: int = Field(ge=1, description="上一页最后一条消息的数据库行 ID。")


class GetGroupHistoryMessagesArgs(StrictModel):
    """获取当前群聊天记录的工具参数。"""

    query_mode: HistoryQueryMode = Field(
        default="recent_count",
        description=(
            "recent_count 最近 N 条；recent_duration 最近分钟；"
            "date_range 北京时间范围；around_message 某消息前后文。"
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "单页返回数量；为空时使用插件配置，around_message 由 before/after 控制。"
        ),
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=1,
        description="recent_duration：回溯分钟数。",
    )
    start_time: str | None = Field(
        default=None,
        description="date_range 开始时间，格式 YYYY-MM-DD HH:MM:SS，北京时间。",
    )
    end_time: str | None = Field(
        default=None,
        description="date_range 结束时间，格式 YYYY-MM-DD HH:MM:SS，北京时间。",
    )
    user_id: str | None = Field(
        default=None,
        description="可选 QQ 号；只保留该成员发言。",
    )
    before: HistoryCursorArgs | None = Field(
        default=None,
        description="读取下一页时原样传回上一页的 next_cursor。",
    )
    context_message_id: str | None = Field(
        default=None,
        description="around_message 锚点消息 ID。",
    )
    before_count: int | None = Field(
        default=None,
        ge=0,
        description="锚点前消息数量；为空时使用插件配置。",
    )
    after_count: int | None = Field(
        default=None,
        ge=0,
        description="锚点后消息数量；为空时使用插件配置。",
    )

    @model_validator(mode="after")
    def check_query_mode_arguments(self) -> "GetGroupHistoryMessagesArgs":
        """校验必填参数，并拒绝当前模式不会读取的输入。"""
        mode_fields = {
            "recent_count": frozenset(("limit", "before")),
            "recent_duration": frozenset(
                ("limit", "duration_minutes", "before")
            ),
            "date_range": frozenset(("limit", "start_time", "end_time", "before")),
            "around_message": frozenset(
                ("context_message_id", "before_count", "after_count")
            ),
        }
        optional_fields = (
            "limit",
            "duration_minutes",
            "start_time",
            "end_time",
            "before",
            "context_message_id",
            "before_count",
            "after_count",
        )
        unused_fields = [
            field_name
            for field_name in optional_fields
            if field_name not in mode_fields[self.query_mode]
            and getattr(self, field_name) is not None
        ]
        if unused_fields:
            raise ValueError(
                f"{self.query_mode} 模式不使用参数: {', '.join(unused_fields)}"
            )
        if self.query_mode == "recent_count":
            return self
        if self.query_mode == "recent_duration":
            if self.duration_minutes is None:
                raise ValueError("recent_duration 模式必须填写 duration_minutes")
            return self
        if self.query_mode == "around_message":
            if self.context_message_id is None:
                raise ValueError("around_message 模式必须填写 context_message_id")
            return self
        if self.start_time is None or self.end_time is None:
            raise ValueError("date_range 模式必须填写 start_time 和 end_time")
        return self
