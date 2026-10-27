"""QQ 公开头像和图床来源；会话目标由运行器授权，URL 仅限官方图床。"""

import httpx

from ..contracts import ConversationScope, JsonObject
from .arguments import ReadMedia

QQ_MEDIA_DOMAINS = ("qpic.cn", "qlogo.cn", "nt.qq.com.cn")


def qq_media_url(source: str) -> httpx.URL:
    try:
        url = httpx.URL(source)
    except httpx.InvalidURL as exc:
        raise ValueError("QQ 图片 URL 格式无效") from exc
    host = url.host.casefold()
    if (
        url.scheme not in ("http", "https")
        or url.username
        or url.password
        or url.port not in (None, 80, 443)
        or not any(host == domain or host.endswith("." + domain) for domain in QQ_MEDIA_DOMAINS)
    ):
        raise ValueError("QQ 图片仅支持无凭据的官方图床 HTTP/HTTPS URL")
    return url


def non_message_segment(scope: ConversationScope, request: ReadMedia) -> JsonObject | None:
    if request.message_id is not None:
        return None
    if request.user_id is not None:
        url = httpx.URL(
            "https://q1.qlogo.cn/g", params={"b": "qq", "nk": request.user_id, "s": "640"}
        )
    elif request.group_avatar:
        if scope.kind != "group":
            raise ValueError("群头像只能在获准的群会话中读取")
        if not scope.peer_id.isascii() or not scope.peer_id.isdecimal():
            raise ValueError("目标群号格式无效")
        url = httpx.URL(f"https://p.qlogo.cn/gh/{scope.peer_id}/{scope.peer_id}/640/")
    elif request.media_url is not None:
        url = qq_media_url(request.media_url)
    else:
        raise ValueError("缺少媒体来源")
    # 此标记只收紧下载范围；不允许通过模型字段指定本地路径或 NapCat 文件标识。
    return {"type": "image", "data": {"url": str(url), "qq_image": True}}
