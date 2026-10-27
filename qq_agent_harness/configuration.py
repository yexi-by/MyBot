"""TOML 增量写回和文件乐观锁；配置是唯一事实来源。"""

import hashlib
import os
import tempfile
import threading
from pathlib import Path

import tomlkit
from pydantic import ValidationError
from tomlkit.container import Container
from tomlkit.exceptions import ConvertError

from .contracts import JsonObject
from .settings import HarnessSettings


class ConfigError(ValueError):
    def __init__(self, issues: list[JsonObject]) -> None:
        super().__init__("配置校验失败")
        self.issues = issues


class ConflictError(ValueError):
    def __init__(self, sha: str) -> None:
        super().__init__("文件已被外部修改")
        self.sha = sha


def checksum(content: bytes) -> str:
    # 仅用于文件乐观锁，绝不作为业务身份或文件存储路径。
    return hashlib.sha256(content).hexdigest()


def merge_table(target: Container, value: dict[str, object]) -> None:
    for key in list(target):
        if key not in value:
            del target[key]
    for key, item in value.items():
        if item is None:
            if key in target:
                del target[key]
        elif isinstance(item, dict):
            current = target.get(key)
            if not isinstance(current, Container) and not hasattr(current, "value"):
                target[key] = tomlkit.table()
            current = target[key]
            if hasattr(current, "value") and isinstance(current.value, Container):
                merge_table(current.value, item)
            elif isinstance(current, Container):
                merge_table(current, item)
            else:
                table = tomlkit.table()
                merge_table(table.value, item)
                target[key] = table
        elif key not in target or target[key] != item:
            target[key] = tomlkit.item(item)


class ConfigStore:
    def __init__(self, path: Path, *, allow_invalid: bool = False) -> None:
        self.path = path.resolve()
        self.root = self.path.parent
        self.lock = threading.RLock()
        self.revision = 0
        self.last_sha = ""
        self.settings: HarnessSettings = HarnessSettings()
        try:
            self.reload()
        except ConfigError:
            if not allow_invalid:
                raise

    def resolve_file(self, relative: str) -> Path:
        requested = Path(relative)
        if requested.is_absolute() or ":" in relative or "\\" in relative:
            raise ValueError("仅允许配置目录内的相对路径")
        resolved = (self.root / requested).resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("路径超出配置目录")
        return resolved

    def validate(self, data: object) -> HarnessSettings:
        try:
            settings = HarnessSettings.model_validate(data)
        except ValidationError as exc:
            issues: list[JsonObject] = []
            for error in exc.errors(include_input=False, include_url=False):
                issues.append(
                    {
                        "path": [str(p) for p in error["loc"]],
                        "message": error["msg"],
                        "type": error["type"],
                    }
                )
            raise ConfigError(issues) from None
        if missing := settings.missing_providers():
            raise ConfigError(
                [
                    {"path": [part for part in path], "message": "所选服务商不存在"}
                    for path in missing
                ]
            )
        for name in settings.persona_paths:
            path = self.resolve_file(name)
            if path == self.path:
                raise ConfigError(
                    [
                        {
                            "path": [
                                "persona_file"
                                if name == settings.persona_file
                                else "knowledge_files"
                            ],
                            "message": "运行配置不能作为提示词或知识库发送给模型",
                        }
                    ]
                )
            if not path.is_file():
                raise ConfigError(
                    [
                        {
                            "path": [
                                "persona_file"
                                if name == settings.persona_file
                                else "knowledge_files"
                            ],
                            "message": "引用文件不存在",
                        }
                    ]
                )
            if name == settings.persona_file and not path.read_text(encoding="utf-8").strip():
                raise ConfigError([{"path": ["persona_file"], "message": "人格提示词不能为空"}])
        return settings

    def reload(self) -> bool:
        with self.lock:
            raw = self.path.read_bytes()
            sha = checksum(raw)
            if sha == self.last_sha:
                return False
            try:
                document = tomlkit.parse(raw.decode("utf-8-sig"))
            except ValueError, UnicodeError:
                raise ConfigError(
                    [{"path": [], "message": "TOML 语法无效，请在文本编辑器中修复配置文件"}]
                ) from None
            settings = self.validate(document.unwrap())
            self.settings = settings
            self.last_sha = sha
            self.revision += 1
            return True

    def snapshot(self) -> JsonObject:
        self.reload()
        return {"config": self.settings.model_dump(mode="json"), "sha": self.last_sha}

    def save(self, data: object, expected_sha: str) -> JsonObject:
        with self.lock:
            current = self.path.read_bytes()
            if checksum(current) != expected_sha:
                raise ConflictError(checksum(current))
            settings = self.validate(data)
            try:
                document = tomlkit.parse(current.decode("utf-8-sig"))
            except ValueError, UnicodeError:
                raise ConfigError(
                    [{"path": [], "message": "请先在文本编辑器中修复 TOML 语法"}]
                ) from None
            try:
                merge_table(document, settings.model_dump(mode="json", exclude_none=True))
                content = tomlkit.dumps(document)
            except ConvertError:
                raise ConfigError(
                    [{"path": [], "message": "配置包含 TOML 不支持的值，例如数组中的 null"}]
                ) from None
            self._write(self.path, content)
            self.reload()
            return self.snapshot()

    def read_file(self, relative: str) -> JsonObject:
        path = self.resolve_file(relative)
        raw = path.read_bytes()
        if len(raw) > self.settings.runtime.config_text_max_bytes:
            raise ValueError("文本文件超过配置编辑预算；可在运行设置中调整 config_text_max_bytes")
        return {"path": relative, "content": raw.decode("utf-8-sig"), "sha": checksum(raw)}

    def save_file(self, relative: str, content: str, expected_sha: str | None) -> JsonObject:
        """expected_sha 为 None 表示只新建；同名文件已存在时按冲突处理，不会覆盖。"""
        with self.lock:
            path = self.resolve_file(relative)
            if not path.parent.is_dir():
                raise ValueError("父目录不存在")
            if path.is_dir():
                raise ValueError("该路径是目录，不能作为文本文件保存")
            if expected_sha is None:
                if path.exists():
                    raise ConflictError(checksum(path.read_bytes()))
            else:
                old = path.read_bytes() if path.exists() else b""
                if checksum(old) != expected_sha:
                    raise ConflictError(checksum(old))
            if len(content.encode("utf-8")) > self.settings.runtime.config_text_max_bytes:
                raise ValueError(
                    "文本文件超过配置编辑预算；可在运行设置中调整 config_text_max_bytes"
                )
            if relative == self.settings.persona_file and not content.strip():
                raise ValueError("人格提示词不能为空")
            self._write(path, content)
            return self.read_file(relative)

    @staticmethod
    def _write(path: Path, text: str) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
