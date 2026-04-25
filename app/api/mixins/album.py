"""NapCat 群相册相关 Action。"""

from app.models import NapCatId, Response

from .base import BaseMixin


class AlbumMixin(BaseMixin):
    """群相册相关 API。"""

    async def del_group_album_media(
        self, group_id: NapCatId, album_id: str, lloc: str
    ) -> None:
        """删除群相册文件。"""
        await self._send_action(
            "del_group_album_media",
            self._build_params(group_id=group_id, album_id=album_id, lloc=lloc),
        )

    async def set_group_album_media_like(
        self,
        group_id: NapCatId,
        album_id: str,
        lloc: str,
        id: str,
        set: bool = True,
    ) -> None:
        """点赞或取消点赞群相册文件。"""
        await self._send_action(
            "set_group_album_media_like",
            self._build_params(
                group_id=group_id, album_id=album_id, lloc=lloc, id=id, set=set
            ),
        )

    async def do_group_album_comment(
        self, group_id: NapCatId, album_id: str, lloc: str, content: str
    ) -> None:
        """发送群相册评论。"""
        await self._send_action(
            "do_group_album_comment",
            self._build_params(
                group_id=group_id, album_id=album_id, lloc=lloc, content=content
            ),
        )

    async def get_group_album_media_list(
        self, group_id: NapCatId, album_id: str, attach_info: str
    ) -> Response:
        """获取群相册文件列表。"""
        return await self._call_action(
            "get_group_album_media_list",
            self._build_params(
                group_id=group_id, album_id=album_id, attach_info=attach_info
            ),
        )

    async def upload_image_to_qun_album(
        self, group_id: NapCatId, album_id: str, album_name: str, file: str
    ) -> None:
        """上传图片到群相册。"""
        await self._send_action(
            "upload_image_to_qun_album",
            self._build_params(
                group_id=group_id,
                album_id=album_id,
                album_name=album_name,
                file=file,
            ),
        )

    async def get_qun_album_list(self, group_id: NapCatId) -> Response:
        """获取群相册列表。"""
        return await self._call_action(
            "get_qun_album_list", self._build_params(group_id=group_id)
        )
