"""测试中构造插件配置管理器的公共辅助。"""

from typing import cast

from app.config import (
    AutoUnbanConfig,
    ConfigManager,
    EmptyPluginConfig,
    GroupNoticeConfig,
    ImageGenerateConfig,
    MaterializedAIGroupChatConfig,
    NeavoImageGenerateConfig,
    PluginConfigSnapshot,
    PluginConfigView,
)


class FakeConfigManager:
    """允许测试显式替换插件配置版本。"""

    def __init__(self, plugins: PluginConfigSnapshot) -> None:
        self.plugins = plugins


def build_plugin_snapshot(
    *,
    revision: int = 1,
    ai_group_chat: MaterializedAIGroupChatConfig | None = None,
    group_notice: GroupNoticeConfig | None = None,
    auto_unban: AutoUnbanConfig | None = None,
    image_generate: ImageGenerateConfig | None = None,
    neavo_image_generate: NeavoImageGenerateConfig | None = None,
    recall_bot_image: EmptyPluginConfig | None = None,
) -> PluginConfigSnapshot:
    """构造不引用磁盘文件的插件配置快照。"""
    return PluginConfigSnapshot(
        revision=revision,
        ai_group_chat=ai_group_chat,
        group_notice=group_notice,
        auto_unban=auto_unban,
        image_generate=image_generate,
        neavo_image_generate=neavo_image_generate,
        recall_bot_image=recall_bot_image,
        referenced_files=frozenset(),
    )


def plugin_config_view(
    fake: FakeConfigManager,
    *,
    plugin_id: str,
) -> PluginConfigView:
    """为测试插件创建只读取自身配置的视图。"""
    return PluginConfigView(
        manager=cast(ConfigManager, fake),
        plugin_id=plugin_id,
    )
