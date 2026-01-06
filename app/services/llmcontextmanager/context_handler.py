from app.services.llm.schemas import ChatMessage
from typing import Literal, overload, Self
from app.utils import base64_to_bytes, load_text_file


class ContextHandler:
    """上下文管理"""

    def __init__(self, system_prompt: str, max_context_length: int) -> None:
        if max_context_length < 2:
            raise ValueError(f"最大上下文必须大于1,当前设置: {max_context_length}")
        self.system_prompt = ChatMessage(role="system", text=system_prompt)
        self._messages_lst = [self.system_prompt]
        self.max_context_length = max_context_length

    @classmethod
    async def new(cls, path: str, max_context_length: int) -> Self:
        system_prompt = await load_text_file(path)
        return cls(system_prompt=system_prompt, max_context_length=max_context_length)

    @property
    def messages_lst(self) -> list[ChatMessage]:
        return self._messages_lst[:]

    def add_msg(self, msg: ChatMessage) -> None:
        self._messages_lst.append(msg)
        if len(self._messages_lst) > self.max_context_length:
            del self._messages_lst[1:3]  # 滑动上下文 删除系统提示词后面的两段提示词

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

    def build_chatmessage(
        self,
        *,
        role: Literal["system", "user", "assistant"] | None = None,
        text: str | None = None,
        image: list[bytes] | list[str] | None = None,
        message: ChatMessage | None = None,
    ) -> None:
        # 将 list[str] 转换为 list[bytes]

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
