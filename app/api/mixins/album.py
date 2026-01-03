"""群相册相关 Mixin 类"""

from app.models.api.payloads import album as album_payload
from app.models.events.response import Response

from .base import BaseMixin


class AlbumMixin(BaseMixin):
    """群相册相关的 API 接口"""

    async def del_group_album_media(
        self, group_id: str, album_id: str, lloc: str
    ) -> None:
        """删除群相册文件"""
        payload = album_payload.DelGroupAlbumMediaPayload(
            params=album_payload.DelGroupAlbumMediaParams(
                group_id=group_id, album_id=album_id, lloc=lloc
            )
        )
        await self._send_payload(payload)

    async def set_group_album_media_like(
        self,
        group_id: str,
        album_id: str,
        lloc: str,
        id: str,
        set: bool = True,
    ) -> None:
        """点赞群相册"""
        payload = album_payload.SetGroupAlbumMediaLikePayload(
            params=album_payload.SetGroupAlbumMediaLikeParams(
                group_id=group_id,
                album_id=album_id,
                lloc=lloc,
                id=id,
                set=set,
            )
        )
        await self._send_payload(payload)

    async def do_group_album_comment(
        self, group_id: str, album_id: str, lloc: str, content: str
    ) -> None:
        """查看群相册评论"""
        payload = album_payload.DoGroupAlbumCommentPayload(
            params=album_payload.DoGroupAlbumCommentParams(
                group_id=group_id, album_id=album_id, lloc=lloc, content=content
            )
        )
        await self._send_payload(payload)

    async def get_group_album_media_list(
        self, group_id: str, album_id: str, attach_info: str
    ) -> Response:
        """获取群相册列表"""
        echo = self._generate_echo()
        payload = album_payload.GetGroupAlbumMediaListPayload(
            params=album_payload.GetGroupAlbumMediaListParams(
                group_id=group_id, album_id=album_id, attach_info=attach_info
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def upload_image_to_qun_album(
        self, group_id: str, album_id: str, album_name: str, file: str
    ) -> None:
        """上传图片到群相册"""
        payload = album_payload.UploadImageToQunAlbumPayload(
            params=album_payload.UploadImageToQunAlbumParams(
                group_id=group_id,
                album_id=album_id,
                album_name=album_name,
                file=file,
            )
        )
        await self._send_payload(payload)

    async def get_qun_album_list(self, group_id: str) -> Response:
        """获取群相册总列表"""
        echo = self._generate_echo()
        payload = album_payload.GetQunAlbumListPayload(
            params=album_payload.GetQunAlbumListParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)
