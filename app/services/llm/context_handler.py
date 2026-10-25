"""LLM 对话上下文管理。"""

from .schemas import ChatMessage


class ContextHandler:
    """维护单个会话的系统提示词与完整上下文。"""

    def __init__(self, system_prompt: str, max_context_tokens: int) -> None:
        """初始化上下文并校验长期上下文的压缩预算。"""
        if max_context_tokens <= 0:
            raise ValueError(f"最大上下文 token 必须大于0,当前设置: {max_context_tokens}")
        self.system_prompt: ChatMessage = ChatMessage(
            role="system", text=system_prompt
        )
        self._messages_lst: list[ChatMessage] = [self.system_prompt]
        self.max_context_tokens: int = max_context_tokens
        self._revision: int = 0

    @property
    def messages_lst(self) -> list[ChatMessage]:
        """返回当前上下文消息的浅拷贝。"""
        return self._messages_lst[:]

    @property
    def revision(self) -> int:
        """返回长期上下文发生实际修改的次数。"""
        return self._revision

    def fork(self) -> "ContextHandler":
        """创建携带当前消息快照的独立临时上下文。"""
        system_prompt = self.system_prompt.text
        if system_prompt is None:
            raise ValueError("系统提示词缺少文本内容")
        forked = ContextHandler(
            system_prompt=system_prompt,
            max_context_tokens=self.max_context_tokens,
        )
        forked._messages_lst = self.messages_lst
        return forked

    def add_msg(
        self, msg: ChatMessage | None = None, msg_list: list[ChatMessage] | None = None
    ) -> None:
        """向上下文追加单条或多条消息。"""
        changed = False
        if msg is not None:
            self._messages_lst.append(msg)
            changed = True
        if msg_list:
            self._messages_lst.extend(msg_list)
            changed = True
        if changed:
            self._revision += 1

    def replace_history(self, *, messages: list[ChatMessage]) -> None:
        """用新的非系统历史替换当前上下文历史。"""
        self._messages_lst = [self.system_prompt, *messages]
        self._revision += 1

    def remove_history_images(self) -> int:
        """移除历史消息中的图片字节，并返回移除的图片数量。"""
        image_count = sum(len(message.image or []) for message in self._messages_lst)
        if image_count == 0:
            return 0
        self._messages_lst = [
            message.without_images() for message in self._messages_lst
        ]
        self._revision += 1
        return image_count
