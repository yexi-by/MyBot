"""BOT 客户端

通过 Mixin 组合方式实现各功能模块的接口。
"""

import asyncio

from fastapi import WebSocket

from app.database import SentMessageRecorder
from app.models import AllEvent, NapCatId, Response
from app.services.napcat import InlineImageArchiver

from .mixins import (
    AccountMixin,
    AlbumMixin,
    BaseMixin,
    FileMixin,
    GroupMixin,
    MessageMixin,
    SystemMixin,
)


class BOTClient(
    MessageMixin,
    GroupMixin,
    FileMixin,
    AlbumMixin,
    AccountMixin,
    SystemMixin,
    BaseMixin,
):
    """BOT 客户端

    通过多重继承组合各功能 Mixin，实现完整的 QQ Bot API 接口。

    Mixins:
        - MessageMixin: 消息相关 API（发送消息、撤回、转发等）
        - GroupMixin: 群聊相关 API（群管理、群成员、群公告等）
        - FileMixin: 文件相关 API（文件上传、下载、管理等）
        - AlbumMixin: 群相册相关 API
        - AccountMixin: 账号相关 API（好友、个人信息等）
        - SystemMixin: 系统相关 API（版本、cookies、密钥等）
    """

    def __init__(
        self,
        websocket: WebSocket,
        sent_message_recorder: SentMessageRecorder,
        inline_image_archiver: InlineImageArchiver,
        send_retry_count: int = 3,
        send_retry_delay: int = 1,
    ) -> None:
        """初始化 BOTClient

        Args:
            websocket: WebSocket 连接实例
            sent_message_recorder: 出站群消息记录接口
            inline_image_archiver: 出站内联图片归档服务
            send_retry_count: NapCat send_msg 发送总尝试次数
            send_retry_delay: NapCat send_msg 发送初始退避秒数
        """
        self.websocket: WebSocket = websocket
        self.sent_message_recorder: SentMessageRecorder = sent_message_recorder
        self.inline_image_archiver: InlineImageArchiver = inline_image_archiver
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.stream_dict: dict[str, asyncio.Queue[Response]] = {}
        self.persistence_failed_event: asyncio.Event = asyncio.Event()
        self.boot_id: NapCatId = ""
        self.timeout: int = 120
        self.send_retry_count: int = send_retry_count
        self.send_retry_delay: int = send_retry_delay

    def get_self_qq_id(self, msg: AllEvent) -> None:
        """从 NapCat 事件中刷新机器人自身 QQ 号。"""
        if not isinstance(msg, Response):
            self.boot_id = msg.self_id
