"""NapCat 群聊工具参数模型。"""

from datetime import timedelta, timezone
from typing import Literal

from pydantic import Field, model_validator

from app.models import NapCatId, StrictModel

MENTION_ALL: Literal["all"] = "all"
MAX_HISTORY_LIMIT: int = 100
BEIJING_TIMEZONE: timezone = timezone(timedelta(hours=8))
HISTORY_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
type HistoryQueryMode = Literal["recent_count", "recent_duration", "date_range"]


class MentionUserArgs(StrictModel):
    """艾特指定群成员或全体成员的工具参数。"""

    user_id: NapCatId | Literal["all"] = Field(
        description="要 @ 的 QQ 号；填写 all 表示 @ 全体成员。"
    )


class ReplyCurrentMessageArgs(StrictModel):
    """引用当前群消息的工具参数。"""


class FinishConversationArgs(StrictModel):
    """结束当前群聊处理的工具参数。"""


class ListGroupRootFilesArgs(StrictModel):
    """获取当前群根目录文件列表的工具参数。"""

    file_count: int = Field(
        default=50,
        ge=1,
        le=200,
        description="需要返回的文件数量，默认 50，最大 200。",
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
    file_count: int = Field(
        default=50,
        ge=1,
        le=200,
        description="需要返回的文件数量，默认 50，最大 200。",
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


class GetGroupHistoryMessagesArgs(StrictModel):
    """获取当前群聊天记录的工具参数。"""

    query_mode: HistoryQueryMode = Field(
        default="recent_count",
        description=(
            "历史消息查询模式。recent_count 表示最近 N 条；"
            "recent_duration 表示最近一段分钟数；date_range 表示指定起止时间范围。"
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=MAX_HISTORY_LIMIT,
        description=(
            "最多返回的消息数量，默认 20，最大 100。"
            "recent_count 模式表示最近 N 条；时间范围模式用于限制返回量。"
        ),
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=1,
        le=10080,
        description="recent_duration 模式使用：向前回溯的分钟数，最大 10080 分钟。",
    )
    start_time: str | None = Field(
        default=None,
        description="date_range 模式使用：开始时间，格式为 YYYY-MM-DD HH:MM:SS，北京时间。",
    )
    end_time: str | None = Field(
        default=None,
        description="date_range 模式使用：结束时间，格式为 YYYY-MM-DD HH:MM:SS，北京时间。",
    )

    @model_validator(mode="after")
    def check_query_mode_arguments(self) -> "GetGroupHistoryMessagesArgs":
        """校验不同历史查询模式所需的参数。"""
        if self.query_mode == "recent_count":
            return self
        if self.query_mode == "recent_duration":
            if self.duration_minutes is None:
                raise ValueError("recent_duration 模式必须填写 duration_minutes")
            return self
        if self.start_time is None or self.end_time is None:
            raise ValueError("date_range 模式必须填写 start_time 和 end_time")
        return self
