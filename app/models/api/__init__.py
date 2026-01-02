"""API 模型包

提供 QQ Bot API 的所有 Payload 模型。

使用方式:
    from app.models.api import payloads

    # 访问各模块
    payloads.base.GroupMessagePayload
    payloads.group.GroupKickPayload
    payloads.file.UploadGroupFilePayload
    payloads.album.GetQunAlbumListPayload
    payloads.account.SendLikePayload
    payloads.system.GetVersionInfoPayload

    # 或者直接导入子模块
    from app.models.api.payloads import base, group, file, album, account, system
    from app.models.api.payloads.base import GroupMessagePayload
"""

from . import payloads
from .other_payload import LoginInfo

__all__ = [
    "payloads",
    "LoginInfo",
]
