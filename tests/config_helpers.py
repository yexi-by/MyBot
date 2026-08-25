"""测试中构造插件配置管理器的公共辅助。"""

from typing import cast
import textwrap

from app.config import (
    AIGroupChatConfig,
    AutoUnbanConfig,
    ConfigManager,
    GroupNoticeConfig,
    ImageGenerateConfig,
    MaterializedAIGroupChatConfig,
    NeavoImageGenerateConfig,
    PluginConfigSnapshot,
    PluginConfigView,
    RecallBotImageConfig,
)


def minimal_config_toml(
    *, group_id: str = "40000", port: int = 6055, with_comments: bool = False
) -> str:
    """生成不引用外部文件且显式给出全部启动参数的 TOML。"""
    service_comment = "# 服务监听配置\n        " if with_comments else ""
    port_comment = "  # NapCat 反向 WS 端口" if with_comments else ""
    plugin_comment = "# 群通知插件\n        " if with_comments else ""
    return textwrap.dedent(
        f"""
        [app]
        name = "MyBot"
        environment = "test"

        {service_comment}[server]
        host = "0.0.0.0"
        port = {port}{port_comment}
        websocket_path_prefix = "/ws"
        access_log = false
        log_level = "info"
        config_watch_debounce_ms = 500
        config_watch_step_ms = 50
        config_watcher_stop_timeout_seconds = 3
        power_action_delay_seconds = 0.5

        [napcat]
        websocket_token = "test-token"
        action_timeout_seconds = 120
        send_max_attempts = 5
        send_retry_delay_seconds = 0
        send_retry_max_delay_seconds = 10
        response_summary_max_chars = 500

        [storage.images]
        directory = "images"
        download_concurrency = 16
        download_timeout_seconds = 20
        max_bytes = 52428800
        retry_delays_seconds = [1, 5, 20]
        lease_seconds = 45
        worker_poll_interval_seconds = 1
        worker_stop_timeout_seconds = 5

        [network]
        proxy = ""
        timeout_seconds = 15

        [logging]
        directory = "logs"
        console_level = "INFO"
        file_level = "DEBUG"
        rotation = "50 MB"
        retention = "30 days"
        compression = "gz"

        [llm.providers.main]
        api_key = "test-api-key"
        inherit_network_proxy = true
        timeout_seconds = 0
        max_attempts = 3
        retry_delay_seconds = 0
        retry_max_delay_seconds = 10

        [mcp]
        enabled = false
        servers = {{}}

        [database]
        host = "localhost"
        port = 5432
        name = "mybot"
        user = "mybot"
        password = "test-password"
        pool_size = 20
        max_overflow = 20
        pool_timeout_seconds = 2
        statement_timeout_seconds = 5
        persistence_retry_delays_seconds = [0.25]

        [plugin_execution]
        stop_timeout_seconds = 3

        [plugin_execution.plugins.ai_group_chat]
        consumers_count = 5
        priority = 5

        [plugin_execution.plugins.group_notice]
        consumers_count = 5
        priority = 10

        [plugin_execution.plugins.auto_unban]
        consumers_count = 1
        priority = 10

        [plugin_execution.plugins.image_generate]
        consumers_count = 5
        priority = 40

        [plugin_execution.plugins.recall_bot_image]
        consumers_count = 5
        priority = 90

        [plugin_execution.plugins.neavo_image_generate]
        consumers_count = 5
        priority = 100

        {plugin_comment}[plugins.group_notice]
        groups = ["{group_id}"]
        send_avatar = true
        """
    ).strip() + "\n"


def build_ai_group_chat_config(
    *,
    supports_images: bool = False,
    provider: str = "main-provider",
    model_name: str = "main-model",
    vision_enabled: bool | None = None,
    overrides: dict[str, object] | None = None,
) -> AIGroupChatConfig:
    """构造字段完整的 AI 群聊测试配置。"""
    use_vision = not supports_images if vision_enabled is None else vision_enabled
    values: dict[str, object] = {
        "model": {
            "provider": provider,
            "name": model_name,
            "supports_images": supports_images,
        },
        "vision": (
            {
                "model": {"provider": "vision-provider", "name": "vision-model"},
                "system_prompt_file": "vision/system.md",
                "user_prompt_file": "vision/user.md",
                "max_attempts": 5,
                "retry_delay_seconds": 0.25,
                "retry_max_delay_seconds": 10,
                "retain_descriptions": True,
            }
            if use_vision
            else None
        ),
        "images": {
            "max_per_turn": 0,
            "fetch_concurrency": 16,
            "download_timeout_seconds": 20,
            "max_image_bytes": 0,
            "max_total_bytes_per_request": 0,
            "max_width": 0,
            "max_height": 0,
            "allowed_mime_types": [],
            "delivery_mode": "vision" if use_vision else "direct",
            "oversize_behavior": "skip",
            "image_detail": "auto",
            "retain_images": False,
            "forward_tool_enabled": True,
            "forward_max_per_call": 0,
            "forward_max_per_turn": 0,
        },
        "formatting": {
            "field_text_limit": 0,
            "json_text_limit": 0,
            "markdown_text_limit": 0,
            "forward_max_items": 0,
            "forward_max_depth": -1,
            "nested_text_search_max_depth": -1,
        },
        "history": {
            "default_limit": 20,
            "max_per_call": 0,
            "default_before_count": 10,
            "default_after_count": 10,
        },
        "files": {
            "default_count": 50,
            "max_per_call": 0,
        },
        "token_estimator": {
            "request_overhead_tokens": 128,
            "message_overhead_tokens": 16,
            "tool_call_overhead_tokens": 64,
            "image_tokens": 1024,
            "ascii_tokens_per_character": 1,
            "non_ascii_tokens_per_character": 2,
        },
        "max_tool_rounds": 16,
        "token_safety_factor": 1.05,
        "context_compression_notice": "正在整理上下文",
        "forward_reply_threshold_chars": 1000,
        "show_reasoning": False,
        "retain_reasoning": False,
        "debug_dump_messages": False,
        "debug_dump_directory": "logs/ai_group_chat_debug",
        "extra_requirements_file": "extra.md",
        "allow_mention_all": False,
        "tool_result_retention": "off",
        "groups": [],
    }
    for key, value in (overrides or {}).items():
        current = values.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            values[key] = {**current, **value}
        else:
            values[key] = value
    return AIGroupChatConfig.model_validate(values)


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
    recall_bot_image: RecallBotImageConfig | None = None,
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
