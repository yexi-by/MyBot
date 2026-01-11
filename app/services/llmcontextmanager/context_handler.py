from app.services.llm.schemas import ChatMessage
from typing import Literal, overload, Self
from app.utils import base64_to_bytes, read_text_file_async


class ContextHandler:
    """上下文管理"""

    def __init__(self, system_prompt: str, max_context_length: int) -> None:
        if max_context_length < 5:
            raise ValueError(f"最大上下文必须大于5,当前设置: {max_context_length}")
        self.system_prompt = ChatMessage(role="system", text=system_prompt)
        self._messages_lst = [self.system_prompt]
        self.max_context_length = max_context_length

    @classmethod
    async def new(cls, path: str, max_context_length: int) -> Self:
        system_prompt = await read_text_file_async(path)
        return cls(system_prompt=system_prompt, max_context_length=max_context_length)

    @property
    def messages_lst(self) -> list[ChatMessage]:
        return self._messages_lst[:]

    def _context_cleanup_algorithm(self, message_lst: list[ChatMessage]) -> None:
        """滑动上下文"""
        start_index: int | None = None
        end_index: int | None = None
        for index, msg in enumerate(message_lst):
            if msg.role == "system":
                continue
            if msg.role == "user":
                if not start_index:
                    start_index = index
                    continue
                else:
                    end_index = index
                    break
        if start_index is not None:
            if end_index is not None:
                del self._messages_lst[start_index:end_index]
            else:
                del self._messages_lst[start_index:]

    def add_msg(
        self, msg: ChatMessage | None = None, msg_list: list[ChatMessage] | None = None
    ) -> None:
        if msg:
            self._messages_lst.append(msg)
        if msg_list:
            self._messages_lst.extend(msg_list)
        if len(self._messages_lst) > self.max_context_length:
            self._context_cleanup_algorithm(message_lst=self._messages_lst)

    @overload
    def build_chatmessage(
        self, *, role: Literal["system", "user", "assistant"], text: str
    ) -> None: ...

    @overload
    def build_chatmessage(
        self,
        *,
        role: Literal["system", "user", "assistant"],
        image: list[bytes] | list[str],
    ) -> None: ...

    @overload
    def build_chatmessage(
        self,
        *,
        role: Literal["system", "user", "assistant"],
        image: list[bytes] | list[str],
        text: str,
    ) -> None: ...
    @overload
    def build_chatmessage(self, *, message: ChatMessage) -> None: ...
    @overload
    def build_chatmessage(self, *, message_lst: list[ChatMessage]) -> None: ...

    def build_chatmessage(
        self,
        *,
        role: Literal["system", "user", "assistant"] | None = None,
        text: str | None = None,
        image: list[bytes] | list[str] | None = None,
        message: ChatMessage | None = None,
        message_lst: list[ChatMessage] | None = None,
    ) -> None:
        if message_lst:
            self.add_msg(msg_list=message_lst)
            return
        if message:
            self.add_msg(msg=message)
            return
        if not role:
            raise ValueError("role在message_list缺失时 必须存在")
        image_bytes: list[bytes] | None = None
        if image:
            if isinstance(image[0], str):
                image_bytes = [base64_to_bytes(data=img) for img in image]  # type: ignore
            else:
                image_bytes = image  # type: ignore
        if role == "system":  # 系统提示词理论上应该只有一份
            if not text or image_bytes:
                raise ValueError("系统提示词应该并且必须是字符串")
            self._messages_lst[0] = ChatMessage(
                role="system",
                text=text,
            )
        else:
            chatmessage = ChatMessage(role=role, text=text, image=image_bytes)
            self.add_msg(msg=chatmessage)

    def del_chatmessage(self, index: int | None = None):
        if not index:
            index = -1
        try:
            del self._messages_lst[index]
        except IndexError:
            raise IndexError("索引超出范围，无法删除对应消息")
