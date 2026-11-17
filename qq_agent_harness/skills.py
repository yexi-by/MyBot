"""配置目录内的按需技能（Skill）扫描与加载；纯文本驱动，不执行任意代码。"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from .configuration import ConfigStore
from .contracts import Model, ToolContext, ToolDefinition, ToolResult, model_binding

CATEGORY_LABELS = {"skills": "技能规范"}
SKILL_TOOL_NAME = "skill__load"


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    relative_path: str


class LoadSkillInput(Model):
    name: str = Field(
        min_length=1,
        max_length=120,
        description="要加载的技能名称（来自上下文中的可用技能目录）",
    )


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def parse_skill_header(content: str, fallback_name: str) -> tuple[str, str, str]:
    """提取可选 YAML frontmatter 中的 name / description 及正文。"""
    normalized = content.lstrip("\ufeff")
    name = fallback_name
    description = ""
    body = normalized
    if normalized.startswith("---\n") or normalized.startswith("---\r\n"):
        lines = normalized.splitlines(keepends=True)
        end_idx: int | None = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx is not None:
            for header_line in lines[1:end_idx]:
                stripped = header_line.strip()
                if not stripped or stripped.startswith("#") or ":" not in stripped:
                    continue
                key, _, raw_val = stripped.partition(":")
                key = key.strip().lower()
                val = _strip_quotes(raw_val)
                if key == "name" and val:
                    name = val
                elif key == "description" and val:
                    description = val
            body = "".join(lines[end_idx + 1 :]).lstrip("\r\n")
    if not description:
        for line in body.splitlines():
            clean = line.strip().lstrip("#").strip()
            if clean:
                description = clean[:160]
                break
    return name, description or "专项操作规范", body


def discover_skills(store: ConfigStore) -> list[SkillSummary]:
    """扫描配置目录 skills/ 下的 Markdown 技能文档，不跟随符号链接。"""
    skills_dir = store.root / "skills"
    if not skills_dir.is_dir() or skills_dir.is_symlink():
        return []
    found: dict[str, SkillSummary] = {}
    candidates: list[Path] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            continue
        if entry.is_file() and entry.suffix.lower() == ".md":
            candidates.append(entry)
        elif entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file() and not skill_md.is_symlink():
                candidates.append(skill_md)
    for path in candidates:
        rel = path.relative_to(store.root).as_posix()
        try:
            resolved = store.resolve_file(rel)
            if resolved == store.path:
                continue
            raw = resolved.read_bytes()
            if len(raw) > store.settings.runtime.config_text_max_bytes:
                continue
            text = raw.decode("utf-8-sig")
        except ValueError, OSError, UnicodeError:
            continue
        fallback = path.parent.name if path.name.upper() == "SKILL.MD" else path.stem
        name, description, _ = parse_skill_header(text, fallback)
        if name and name not in found:
            found[name] = SkillSummary(name=name, description=description, relative_path=rel)
    return [found[key] for key in sorted(found)]


def build_tools(get_store: Callable[[], ConfigStore]) -> list[ToolDefinition]:
    async def load_skill(context: ToolContext, request: LoadSkillInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        store = get_store()
        target_name = request.name.strip()
        catalog = discover_skills(store)
        matched = next(
            (
                item
                for item in catalog
                if item.name == target_name or item.name.casefold() == target_name.casefold()
            ),
            None,
        )
        if matched is None:
            return ToolResult.failure(
                "未找到指定技能，请核对可用技能列表",
                "SkillNotFound",
            ).model_copy(
                update={
                    "data": {
                        "available_skills": [
                            {"name": item.name, "description": item.description} for item in catalog
                        ]
                    }
                }
            )
        resolved = store.resolve_file(matched.relative_path)
        text = resolved.read_text(encoding="utf-8-sig")
        _, _, body = parse_skill_header(text, matched.name)
        return ToolResult.success(
            {
                "name": matched.name,
                "description": matched.description,
                "path": matched.relative_path,
                "instructions": body.strip() or text.strip(),
            }
        )

    return [
        ToolDefinition(
            SKILL_TOOL_NAME,
            "按名称加载配置目录中的专项技能规范（SOP、脚本模板与领域约定）。仅在需要时读取，避免无关长文档常驻占用上下文。",
            "skills",
            model_binding(LoadSkillInput, load_skill),
            concurrency_safe=True,
            title="读取技能规范",
        )
    ]
