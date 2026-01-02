from app.models import GroupMessage

from ..base import BasePlugin


class HelloPlugin(BasePlugin[GroupMessage]):
    name = "HelloPlugin"
    consumers_count = 5
    priority = 10

    def setup(self) -> None: ...

    async def run(self, msg: GroupMessage) -> bool:
        for m in msg.message:
            if m.type == "text" and m.data.text == "你好":
                await self.bot.send_msg(
                    group_id=msg.group_id, text="你好世界", at=msg.user_id
                )
                return True
        return False
