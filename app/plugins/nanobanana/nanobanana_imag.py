from app.models import GroupMessage
from ..base import BasePlugin
from pydantic_settings import BaseSettings
from app.services.llm.schemas import ChatMessage
from pydantic import BaseModel
from app.utils import load_config
from ..utils import aggregate_messages, extract_text_from_message

GROUP_CONFIG_PATH = "plugins_config/nanobanana_config.toml"
TEXT_IMAGE_TOKEN = "/香蕉生图"


class GroupConfig(BaseModel):
    group_id: int


class PluginConfig(BaseSettings):
    group_config: list[GroupConfig]


class BananaImage(BasePlugin[GroupMessage]):
    name = "banana生图插件"
    consumers_count = 1
    priority = 10

    def setup(self) -> None:
        self.config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        self.group_list = [
            group_config.group_id for group_config in self.config.group_config
        ]

    async def get_nanobanana_image(self, prompt: str, group_id: int) -> None:
        message = ChatMessage(role="user", text=prompt)
        image_base64 = self.context.llm.get_image(
            message=message, model="gemini-3-pro-image-4k", model_vendors="Antigravity"
        )
        file_image_base = f"base64://{image_base64}"
        await self.context.bot.send_msg(group_id=group_id, image=file_image_base)

    async def run(self, msg: GroupMessage) -> bool:
        group_id = msg.group_id
        user_id = msg.user_id
        if group_id not in self.group_list:
            return False
        at_lst, text_list, image_url_lst, reply_id = aggregate_messages(msg=msg)
        text = "".join(text_list)
        prompt = extract_text_from_message(text=text, token=TEXT_IMAGE_TOKEN)
        if not prompt:
            return False
        await self.context.bot.send_msg(
            group_id=group_id, at=user_id, text="正在生成图片...."
        )
        await self.get_nanobanana_image(prompt=text, group_id=group_id)

        return True
