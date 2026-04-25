"""LLM 对话上下文管理。"""

from typing import Literal, Self, overload

from app.utils.encoding import base64_to_bytes
from app.utils.files import read_text_file_async

from .schemas import ChatMessage

ChatRole = Literal["system", "user", "assistant"]


class ContextHandler:
    """维护单个会话的系统提示词与滑动上下文。"""

    def __init__(self, system_prompt: str, max_context_length: int) -> None:
        """初始化上下文并校验最大上下文长度。"""
        if max_context_length < 5:
            raise ValueError(f"最大上下文必须大于5,当前设置: {max_context_length}")
        self.system_prompt: ChatMessage = ChatMessage(
            role="system", text=system_prompt
        )
        self._messages_lst: list[ChatMessage] = [self.system_prompt]
        self.max_context_length: int = max_context_length

    @classmethod
    async def new(cls, path: str, max_context_length: int) -> Self:
        """从系统提示词文件创建上下文管理器。"""
        system_prompt = await read_text_file_async(path)
        return cls(system_prompt=system_prompt, max_context_length=max_context_length)

    @property
    def messages_lst(self) -> list[ChatMessage]:
        """返回当前上下文消息的浅拷贝。"""
        return self._messages_lst[:]

    def _context_cleanup_algorithm(self, message_lst: list[ChatMessage]) -> None:
        """按用户轮次滑动删除最早的一段非系统上下文。"""
        start_index: int | None = None
        end_index: int | None = None
        for index, msg in enumerate(message_lst):
            if msg.role == "system":
                continue
            if msg.role == "user":
                if start_index is None:
                    start_index = index
                    continue
                end_index = index
                break
        if start_index is None:
            return
        if end_index is not None:
            del self._messages_lst[start_index:end_index]
            return
        del self._messages_lst[start_index:]

    def add_msg(
        self, msg: ChatMessage | None = None, msg_list: list[ChatMessage] | None = None
    ) -> None:
        """向上下文追加单条或多条消息，并在超长时触发滑动清理。"""
        if msg is not None:
            self._messages_lst.append(msg)
        if msg_list is not None:
            self._messages_lst.extend(msg_list)
        if len(self._messages_lst) > self.max_context_length:
            self._context_cleanup_algorithm(message_lst=self._messages_lst)

    @overload
    def build_chatmessage(self, *, role: ChatRole, text: str) -> None:
        """追加纯文本聊天消息。"""
        ...

    @overload
    def build_chatmessage(
        self, *, role: ChatRole, image: list[bytes] | list[str]
    ) -> None:
        """追加纯图片聊天消息。"""
        ...

    @overload
    def build_chatmessage(
        self,
        *,
        role: ChatRole,
        image: list[bytes] | list[str],
        text: str,
    ) -> None:
        """追加图文混合聊天消息。"""
        ...

    @overload
    def build_chatmessage(self, *, message: ChatMessage) -> None:
        """追加已经构造好的聊天消息。"""
        ...

    @overload
    def build_chatmessage(self, *, message_lst: list[ChatMessage]) -> None:
        """批量追加已经构造好的聊天消息。"""
        ...

    def build_chatmessage(
        self,
        *,
        role: ChatRole | None = None,
        text: str | None = None,
        image: list[bytes] | list[str] | None = None,
        message: ChatMessage | None = None,
        message_lst: list[ChatMessage] | None = None,
    ) -> None:
        """根据结构化参数构造消息并写入上下文。"""
        if message_lst is not None:
            self.add_msg(msg_list=message_lst)
            return
        if message is not None:
            self.add_msg(msg=message)
            return
        if role is None:
            raise ValueError("role在message_list缺失时 必须存在")

        image_bytes = self._normalize_images(image=image)
        if role == "system":
            if not text or image_bytes is not None:
                raise ValueError("系统提示词应该并且必须是字符串")
            self._messages_lst[0] = ChatMessage(role="system", text=text)
            return

        chatmessage = ChatMessage(role=role, text=text, image=image_bytes)
        self.add_msg(msg=chatmessage)

    def _normalize_images(
        self, image: list[bytes] | list[str] | None
    ) -> list[bytes] | None:
        """将上下文图片统一收窄为字节列表。"""
        if image is None:
            return None
        image_bytes: list[bytes] = []
        for item in image:
            if isinstance(item, str):
                image_bytes.append(base64_to_bytes(data=item))
                continue
            image_bytes.append(item)
        return image_bytes

    def del_chatmessage(self, index: int | None = None) -> None:
        """删除指定下标的上下文消息，默认删除最后一条。"""
        target_index = -1 if index is None else index
        try:
            del self._messages_lst[target_index]
        except IndexError as exc:
            raise IndexError("索引超出范围，无法删除对应消息") from exc
