"""统一配置加载、文件引用解析和插件配置版本管理。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from .schemas import (
    AIGroupChatConfig,
    AIGroupConfig,
    AutoUnbanConfig,
    EmptyPluginConfig,
    GroupNoticeConfig,
    ImageGenerateConfig,
    ModelRef,
    MyBotConfig,
    NeavoImageGenerateConfig,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIRECTORY = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIRECTORY / "mybot.toml"
_RESTART_ONLY_SECTIONS = (
    "app",
    "server",
    "napcat",
    "storage",
    "network",
    "logging",
    "llm",
    "mcp",
    "database",
)


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """一项不包含原始输入值的安全配置错误。"""

    location: str
    error_type: str
    message: str


class ConfigLoadError(RuntimeError):
    """配置文件无法安全加载或校验。"""

    def __init__(self, issues: tuple[ConfigIssue, ...]) -> None:
        """保存已经脱敏的配置错误。"""
        self.issues = issues
        summary = "；".join(
            f"{issue.location}: {issue.message}" for issue in issues
        )
        super().__init__(summary or "配置加载失败")


@dataclass(frozen=True, slots=True)
class MaterializedAIGroupConfig:
    """已经读取 prompt 和知识库内容的单群配置。"""

    source: AIGroupConfig
    system_prompt: str


@dataclass(frozen=True, slots=True)
class MaterializedAIGroupChatConfig:
    """AI 群聊处理事件时不再需要访问文件的配置。"""

    source: AIGroupChatConfig
    groups: tuple[MaterializedAIGroupConfig, ...]
    vision_system_prompt: str | None
    vision_user_prompt: str | None


type PluginConfigValue = (
    MaterializedAIGroupChatConfig
    | GroupNoticeConfig
    | AutoUnbanConfig
    | ImageGenerateConfig
    | NeavoImageGenerateConfig
    | EmptyPluginConfig
)


@dataclass(frozen=True, slots=True)
class PluginConfigSnapshot:
    """一次成功加载后所有插件共享的配置版本。"""

    revision: int
    ai_group_chat: MaterializedAIGroupChatConfig | None
    group_notice: GroupNoticeConfig | None
    auto_unban: AutoUnbanConfig | None
    image_generate: ImageGenerateConfig | None
    neavo_image_generate: NeavoImageGenerateConfig | None
    recall_bot_image: EmptyPluginConfig | None
    referenced_files: frozenset[Path]

    def get(self, plugin_id: str) -> PluginConfigValue | None:
        """按稳定 plugin_id 返回当前配置。"""
        match plugin_id:
            case "ai_group_chat":
                return self.ai_group_chat
            case "group_notice":
                return self.group_notice
            case "auto_unban":
                return self.auto_unban
            case "image_generate":
                return self.image_generate
            case "neavo_image_generate":
                return self.neavo_image_generate
            case "recall_bot_image":
                return self.recall_bot_image
            case _:
                raise KeyError(f"插件没有统一配置定义: {plugin_id}")


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """完整启动配置和已经读取文件内容的插件配置。"""

    config: MyBotConfig
    plugins: PluginConfigSnapshot


@dataclass(frozen=True, slots=True)
class ConfigReloadResult:
    """一次自动重载的结果。"""

    applied: bool
    revision: int
    changed_plugins: tuple[str, ...] = ()
    restart_required_sections: tuple[str, ...] = ()
    error: ConfigLoadError | None = None


def _safe_validation_issues(exc: ValidationError) -> tuple[ConfigIssue, ...]:
    """只保留 Pydantic 错误位置、类型和说明。"""
    issues: list[ConfigIssue] = []
    for item in exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = ".".join(str(part) for part in item.get("loc", ())) or "config"
        issues.append(
            ConfigIssue(
                location=location,
                error_type=str(item.get("type", "validation_error")),
                message=str(item.get("msg", "配置字段无效")),
            )
        )
    return tuple(issues)


def _resolve_config_file(
    *, config_root: Path, file_value: str, label: str
) -> Path:
    """把配置文件引用限制在统一配置目录内。"""
    candidate = Path(file_value)
    if candidate.is_absolute():
        raise ConfigLoadError(
            (ConfigIssue(label, "absolute_path", "必须使用相对 config 目录的路径"),)
        )
    try:
        root = config_root.resolve(strict=True)
        resolved = (root / candidate).resolve(strict=True)
    except OSError as exc:
        raise ConfigLoadError(
            (ConfigIssue(label, type(exc).__name__, "引用的文件不存在或无法读取"),)
        ) from exc
    if not resolved.is_relative_to(root):
        raise ConfigLoadError(
            (ConfigIssue(label, "path_escape", "引用文件不能位于 config 目录外"),)
        )
    if not resolved.is_file():
        raise ConfigLoadError(
            (ConfigIssue(label, "not_a_file", "引用路径不是普通文件"),)
        )
    return resolved


def _read_config_text(
    *,
    config_root: Path,
    file_value: str,
    label: str,
    require_content: bool,
) -> tuple[Path, str]:
    """读取 UTF-8 配置文本，并按消费者要求检查空内容。"""
    path = _resolve_config_file(
        config_root=config_root,
        file_value=file_value,
        label=label,
    )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigLoadError(
            (ConfigIssue(label, type(exc).__name__, "文件不是可读取的 UTF-8 文本"),)
        ) from exc
    if require_content and content.strip() == "":
        raise ConfigLoadError(
            (ConfigIssue(label, "empty_file", "文件内容不能为空"),)
        )
    return path, content


def _validate_model_ref(
    *, reference: ModelRef, provider_ids: frozenset[str], label: str
) -> None:
    """确保插件只能引用当前进程已经注册的 provider。"""
    if reference.provider not in provider_ids:
        raise ConfigLoadError(
            (
                ConfigIssue(
                    f"{label}.provider",
                    "unknown_provider",
                    f"provider {reference.provider!r} 未在当前进程中注册",
                ),
            )
        )


def _materialize_plugins(
    *,
    config: MyBotConfig,
    config_file: Path,
    provider_ids: frozenset[str],
    revision: int,
) -> PluginConfigSnapshot:
    """校验 provider 引用并读取插件实际消费的文本文件。"""
    config_root = config_file.parent
    referenced_files: set[Path] = {config_file.resolve(strict=False)}
    ai_config = config.plugins.ai_group_chat
    materialized_ai: MaterializedAIGroupChatConfig | None = None
    if ai_config is not None:
        _validate_model_ref(
            reference=ai_config.model,
            provider_ids=provider_ids,
            label="plugins.ai_group_chat.model",
        )
        extra_path, extra_requirements = _read_config_text(
            config_root=config_root,
            file_value=ai_config.extra_requirements_file,
            label="plugins.ai_group_chat.extra_requirements_file",
            require_content=True,
        )
        referenced_files.add(extra_path)
        vision_system_prompt: str | None = None
        vision_user_prompt: str | None = None
        if ai_config.vision is not None:
            _validate_model_ref(
                reference=ai_config.vision.model,
                provider_ids=provider_ids,
                label="plugins.ai_group_chat.vision.model",
            )
            vision_system_path, vision_system_prompt = _read_config_text(
                config_root=config_root,
                file_value=ai_config.vision.system_prompt_file,
                label="plugins.ai_group_chat.vision.system_prompt_file",
                require_content=True,
            )
            vision_user_path, vision_user_prompt = _read_config_text(
                config_root=config_root,
                file_value=ai_config.vision.user_prompt_file,
                label="plugins.ai_group_chat.vision.user_prompt_file",
                require_content=True,
            )
            referenced_files.update((vision_system_path, vision_user_path))

        materialized_groups: list[MaterializedAIGroupConfig] = []
        for index, group in enumerate(ai_config.groups):
            system_path, system_prompt = _read_config_text(
                config_root=config_root,
                file_value=group.system_prompt_file,
                label=f"plugins.ai_group_chat.groups.{index}.system_prompt_file",
                require_content=True,
            )
            referenced_files.add(system_path)
            prompt_parts = [system_prompt]
            if group.knowledge_base_file is not None:
                knowledge_path, knowledge_base = _read_config_text(
                    config_root=config_root,
                    file_value=group.knowledge_base_file,
                    label=f"plugins.ai_group_chat.groups.{index}.knowledge_base_file",
                    require_content=False,
                )
                referenced_files.add(knowledge_path)
                if knowledge_base.strip() != "":
                    prompt_parts.append(knowledge_base)
            prompt_parts.append(extra_requirements)
            materialized_groups.append(
                MaterializedAIGroupConfig(
                    source=group,
                    system_prompt="\n\n".join(prompt_parts),
                )
            )
        materialized_ai = MaterializedAIGroupChatConfig(
            source=ai_config,
            groups=tuple(materialized_groups),
            vision_system_prompt=vision_system_prompt,
            vision_user_prompt=vision_user_prompt,
        )

    image_generate = config.plugins.image_generate
    if image_generate is not None:
        _validate_model_ref(
            reference=image_generate.model,
            provider_ids=provider_ids,
            label="plugins.image_generate.model",
        )

    return PluginConfigSnapshot(
        revision=revision,
        ai_group_chat=materialized_ai,
        group_notice=config.plugins.group_notice,
        auto_unban=config.plugins.auto_unban,
        image_generate=image_generate,
        neavo_image_generate=config.plugins.neavo_image_generate,
        recall_bot_image=config.plugins.recall_bot_image,
        referenced_files=frozenset(referenced_files),
    )


def _load_config_model(*, config_file: Path) -> MyBotConfig:
    """读取唯一 TOML 文件并完成不涉及外部文件的模型校验。"""
    try:
        with config_file.open("rb") as file:
            raw_config = cast(dict[str, object], tomllib.load(file))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigLoadError(
            (ConfigIssue(str(config_file), "toml_decode", str(exc)),)
        ) from exc
    except OSError as exc:
        raise ConfigLoadError(
            (ConfigIssue(str(config_file), type(exc).__name__, "配置文件无法读取"),)
        ) from exc
    try:
        return MyBotConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigLoadError(_safe_validation_issues(exc)) from exc


def _candidate_plugin_files(*, config_file: Path) -> frozenset[Path]:
    """从当前 TOML 收集可安全监听但尚未成功读取的插件文件。"""
    try:
        config = _load_config_model(config_file=config_file)
    except ConfigLoadError:
        return frozenset()
    ai_config = config.plugins.ai_group_chat
    if ai_config is None:
        return frozenset()
    file_values: list[str] = [ai_config.extra_requirements_file]
    if ai_config.vision is not None:
        file_values.extend(
            (
                ai_config.vision.system_prompt_file,
                ai_config.vision.user_prompt_file,
            )
        )
    for group in ai_config.groups:
        file_values.append(group.system_prompt_file)
        if group.knowledge_base_file is not None:
            file_values.append(group.knowledge_base_file)

    root = config_file.parent.resolve(strict=False)
    candidates: set[Path] = set()
    for file_value in file_values:
        relative = Path(file_value)
        if relative.is_absolute():
            continue
        resolved = (root / relative).resolve(strict=False)
        if resolved.is_relative_to(root):
            candidates.add(resolved)
    return frozenset(candidates)


def load_config(
    *,
    config_file: Path = CONFIG_FILE,
    active_provider_ids: frozenset[str] | None = None,
    revision: int = 1,
) -> LoadedConfig:
    """读取唯一 TOML 文件并构造完整配置。"""
    config = _load_config_model(config_file=config_file)
    provider_ids = (
        frozenset(config.llm.providers)
        if active_provider_ids is None
        else active_provider_ids
    )
    plugins = _materialize_plugins(
        config=config,
        config_file=config_file,
        provider_ids=provider_ids,
        revision=revision,
    )
    return LoadedConfig(config=config, plugins=plugins)


def _changed_plugins(
    previous: PluginConfigSnapshot, current: PluginConfigSnapshot
) -> tuple[str, ...]:
    """返回实际发生变化的插件 ID。"""
    plugin_ids = (
        "ai_group_chat",
        "group_notice",
        "auto_unban",
        "image_generate",
        "neavo_image_generate",
        "recall_bot_image",
    )
    return tuple(
        plugin_id
        for plugin_id in plugin_ids
        if previous.get(plugin_id) != current.get(plugin_id)
    )


class ConfigManager:
    """保存启动配置，并原子发布通过校验的插件配置。"""

    def __init__(self, *, loaded: LoadedConfig, config_file: Path = CONFIG_FILE) -> None:
        """保存启动时完整配置和首个插件配置版本。"""
        self.config_file = config_file.absolute()
        self.config_root = self.config_file.parent
        self.boot_config = loaded.config
        self._plugins = loaded.plugins
        self._watched_files = loaded.plugins.referenced_files

    @classmethod
    def create(cls, *, config_file: Path = CONFIG_FILE) -> "ConfigManager":
        """从唯一配置文件创建管理器。"""
        return cls(loaded=load_config(config_file=config_file), config_file=config_file)

    @property
    def plugins(self) -> PluginConfigSnapshot:
        """返回当前有效的插件配置快照。"""
        return self._plugins

    @property
    def watched_files(self) -> frozenset[Path]:
        """返回已生效引用和最近一次合法候选引用的并集。"""
        return self._watched_files

    def bind_plugin(self, plugin_id: str) -> "PluginConfigView":
        """创建只能读取指定插件配置的稳定视图。"""
        _ = self._plugins.get(plugin_id)
        return PluginConfigView(manager=self, plugin_id=plugin_id)

    def reload(self) -> ConfigReloadResult:
        """完整校验文件，并只发布插件配置变化。"""
        next_revision = self._plugins.revision + 1
        try:
            loaded = load_config(
                config_file=self.config_file,
                active_provider_ids=frozenset(self.boot_config.llm.providers),
                revision=next_revision,
            )
        except ConfigLoadError as exc:
            self._watched_files = frozenset(
                set(self._plugins.referenced_files)
                | set(_candidate_plugin_files(config_file=self.config_file))
            )
            return ConfigReloadResult(
                applied=False,
                revision=self._plugins.revision,
                error=exc,
            )
        restart_required = tuple(
            section
            for section in _RESTART_ONLY_SECTIONS
            if getattr(loaded.config, section) != getattr(self.boot_config, section)
        )
        changed_plugins = _changed_plugins(self._plugins, loaded.plugins)
        self._watched_files = loaded.plugins.referenced_files
        if not changed_plugins:
            return ConfigReloadResult(
                applied=False,
                revision=self._plugins.revision,
                restart_required_sections=restart_required,
            )
        self._plugins = replace(loaded.plugins, revision=next_revision)
        return ConfigReloadResult(
            applied=True,
            revision=next_revision,
            changed_plugins=changed_plugins,
            restart_required_sections=restart_required,
        )


class PluginConfigView:
    """按 plugin_id 收窄的只读热加载配置入口。"""

    __slots__ = ("_manager", "_plugin_id")

    def __init__(self, *, manager: ConfigManager, plugin_id: str) -> None:
        """绑定单个内置插件，且不暴露完整启动配置。"""
        self._manager = manager
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        """返回不可修改的插件身份。"""
        return self._plugin_id

    @property
    def revision(self) -> int:
        """返回当前插件配置快照版本。"""
        return self._manager.plugins.revision

    def get[ConfigT: PluginConfigValue](
        self, expected_type: type[ConfigT]
    ) -> ConfigT | None:
        """读取自己的配置，并校验调用方声明的配置类型。"""
        value = self._manager.plugins.get(self._plugin_id)
        if value is None:
            return None
        if not isinstance(value, expected_type):
            raise TypeError(
                f"插件 {self._plugin_id} 配置类型不匹配，期望 {expected_type.__name__}"
            )
        return value
