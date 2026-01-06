import json
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter
from pydantic_settings import BaseSettings

from app.models import At, GroupMessage, MessageSegment, Reply, Text
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import download_image, load_text_file_sync, logger

from ..base import BasePlugin


class SendMessage(BaseModel):
    text: Annotated[
        str | None,
        Field(description="发送的群聊文本内容。如果只想艾特人而不说话，可以为空字符串"),
    ] = None
    at: Annotated[
        int | Literal["all"] | None,
        Field(description="要艾特的人的qq号。填 'all' 则艾特所有人"),
    ] = None
    reply: Annotated[
        int | None,
        Field(
            description="需要回复的那条消息的ID,注意:回复会导致自动艾特,因此在回复和艾特同一个人的时候,只需要回复就可以了,会自动艾特的"
        ),
    ] = None


class AImessage(BaseModel):
    send_message: Annotated[
        SendMessage | None,
        Field(description="如果判断需要向群聊发送消息,则填充此字段;无话可说，则留空"),
    ] = None


class GroupConfig(BaseModel):
    group_id: int
    system_prompt_path: str
    knowledge_base_path: str
    function_path: str
    max_context_length: int


class Config(BaseSettings):
    group_config: list[GroupConfig]


class AiGroupMessage(BasePlugin[GroupMessage]):
    name = "ai回复插件"
    consumers_count = 5
    priority = 5

    def setup(self) -> None:
        self.group_config: dict[int, ContextHandler] = {}
        toml_data = self.load_toml_file(file_path="other/group_config.toml")
        config = Config(**toml_data)
        schema = AImessage.model_json_schema()
        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

        for g in config.group_config:
            system_prompt = load_text_file_sync(file_path=g.system_prompt_path)
            knowledge_base = load_text_file_sync(file_path=g.knowledge_base_path)
            function_path = load_text_file_sync(file_path=g.function_path)
            all_prompt = "\n\n".join(
                [system_prompt, knowledge_base, function_path, schema_str]
            )
            self.group_config[g.group_id] = ContextHandler(
                system_prompt=all_prompt, max_context_length=g.max_context_length
            )

    def load_toml_file(self, file_path: str | Path) -> dict:
        path = Path(file_path)
        with open(path, "rb") as f:
            toml_data = tomllib.load(f)
            return toml_data

    async def get_image(self, msg: GroupMessage) -> ChatMessage:
        image: list[bytes] = []
        for segment in msg.message:
            if segment.type == "image" and segment.data.url is not None:
                image_bytes = await download_image(
                    url=segment.data.url, client=self.context.direct_httpx
                )
                image.append(image_bytes)
        text = msg.model_dump_json()
        chat_message = ChatMessage(role="user", image=image, text=text)

        return chat_message

    async def get_ai_repose(self, msg: GroupMessage, context: ContextHandler) -> bool:
        chat_message = None
        for segment in msg.message:
            if segment.type != "at" or segment.data.qq != self.context.bot.boot_id:
                continue
            chat_message = await self.get_image(msg=msg)
            break
        if not chat_message:
            return False
        messages = context.messages_lst
        messages.append(chat_message)
        count = 0
        while count <= 10:
            count += 1
            response = await self.context.llm.get_ai_text_response(
                messages=messages,
                model_name="gemini-3-pro-preview",
                model_vendors="google",
            )
            try:
                logger.debug(response)
                ai_msg = AImessage.model_validate_json(response)
                message_segment: list[MessageSegment] = []
                if ai_msg.send_message is None:
                    break
                text = ai_msg.send_message.text
                at = ai_msg.send_message.at
                reply = ai_msg.send_message.reply
                if text:
                    text_segment = Text.new(text=text)
                    message_segment.append(text_segment)
                if at:
                    at_segment = At.new(qq=at)
                    message_segment.append(at_segment)
                if reply:
                    reply_segment = Reply.new(id=reply)
                    message_segment.append(reply_segment)

                await self.context.bot.send_msg(
                    group_id=msg.group_id, message_segment=message_segment
                )
                context.build_chatmessage(message=chat_message)
                adapter = TypeAdapter(list[MessageSegment])
                json_str = adapter.dump_json(message_segment).decode("utf-8")
                context.build_chatmessage(role="assistant", text=json_str)
                break
            except Exception as e:
                error_message = ChatMessage(role="user", text=f"错误:{e}")
                messages.append(error_message)
        return True

    async def run(self, msg: GroupMessage) -> bool:
        if msg.group_id not in self.group_config:
            return False
        context = self.group_config[msg.group_id]
        return await self.get_ai_repose(msg=msg, context=context)
