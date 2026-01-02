"""BOT 客户端

通过 Mixin 组合方式实现各功能模块的接口。
"""

import asyncio

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models import AllEvent, LifeCycle, Response

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

    def __init__(self, websocket: WebSocket, database: RedisDatabaseManager) -> None:
        """初始化 BOTClient

        Args:
            websocket: WebSocket 连接实例
            database: Redis 数据库管理器实例
        """
        self.websocket = websocket
        self.database = database
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.boot_id: int = 0
        self.timeout: int = 20

    def get_self_qq_id(self, msg: AllEvent) -> None:
        """获取自身 QQ 号，外部接口"""
        if isinstance(msg, LifeCycle):
            self.boot_id = msg.self_id
