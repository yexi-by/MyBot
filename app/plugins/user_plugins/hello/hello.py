from ...base import BasePlugin
from app.models import GroupMessage


class HelloPlugin(BasePlugin[GroupMessage]):
    name = "HelloPlugin"
    consumers_count = 5
    priority = 10

    def setup(self) -> None: ...

    async def run(self, data: GroupMessage) -> bool:
        for msg in data.message:
            if msg.type == "text" and msg.data.text == "你好":
                await self.bot.send_msg(group_id=data.group_id, text="你好世界")
                return True
        return False
