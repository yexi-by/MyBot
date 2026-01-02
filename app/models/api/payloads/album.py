"""群相册相关 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 删除群相册文件 ====================
class DelGroupAlbumMediaParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str


class DelGroupAlbumMediaPayload(BaseModel):
    """删除群相册文件"""

    action: Literal["del_group_album_media"] = "del_group_album_media"
    params: DelGroupAlbumMediaParams


# ==================== 点赞群相册 ====================
class SetGroupAlbumMediaLikeParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str
    id: str
    set: bool = True


class SetGroupAlbumMediaLikePayload(BaseModel):
    """点赞群相册"""

    action: Literal["set_group_album_media_like"] = "set_group_album_media_like"
    params: SetGroupAlbumMediaLikeParams


# ==================== 查看群相册评论 ====================
class DoGroupAlbumCommentParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str
    content: str


class DoGroupAlbumCommentPayload(BaseModel):
    """查看群相册评论"""

    action: Literal["do_group_album_comment"] = "do_group_album_comment"
    params: DoGroupAlbumCommentParams


# ==================== 获取群相册列表 ====================
class GetGroupAlbumMediaListParams(BaseModel):
    group_id: str
    album_id: str
    attach_info: str


class GetGroupAlbumMediaListPayload(BaseModel):
    """获取群相册列表"""

    action: Literal["get_group_album_media_list"] = "get_group_album_media_list"
    params: GetGroupAlbumMediaListParams
    echo: str


# ==================== 上传图片到群相册 ====================
class UploadImageToQunAlbumParams(BaseModel):
    group_id: str
    album_id: str
    album_name: str
    file: str


class UploadImageToQunAlbumPayload(BaseModel):
    """上传图片到群相册"""

    action: Literal["upload_image_to_qun_album"] = "upload_image_to_qun_album"
    params: UploadImageToQunAlbumParams


# ==================== 获取群相册总列表 ====================
class GetQunAlbumListParams(BaseModel):
    group_id: str


class GetQunAlbumListPayload(BaseModel):
    """获取群相册总列表"""

    action: Literal["get_qun_album_list"] = "get_qun_album_list"
    params: GetQunAlbumListParams
    echo: str
