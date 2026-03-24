import logging
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

import yaml

log = logging.getLogger(__name__)

DEFAULT_FILE_SKILLS_DIR = Path.home() / ".open-webui" / "skills"
MAX_FILE_SKILL_DEPTH = 6
MAX_FILE_SKILL_DIRS = 2000


@dataclass(frozen=True)
class FileSkill:
    name: str
    description: str
    content: str
    path: Path
    skill_md_path: Path


_CACHE_LOCK = Lock()
_DISCOVERY_CACHE: dict[str, dict] = {}


def get_file_skills_dir() -> Path:
    return Path(os.getenv("FILE_SKILLS_DIR") or DEFAULT_FILE_SKILLS_DIR).expanduser()


def parse_skill_md(path: Path) -> Optional[FileSkill]:
    try:
        skill_md_path = path.expanduser().resolve()
        content = skill_md_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"Failed to read file skill {path}: {e}")
        return None

    parsed = _split_frontmatter(content)
    if parsed is None:
        log.warning(f"Skipping file skill without valid frontmatter: {skill_md_path}")
        return None

    frontmatter, body = parsed
    try:
        metadata = yaml.safe_load(frontmatter)
    except yaml.YAMLError as e:
        log.warning(f"Skipping file skill with invalid YAML frontmatter {skill_md_path}: {e}")
        return None

    if not isinstance(metadata, dict):
        log.warning(f"Skipping file skill with non-object frontmatter: {skill_md_path}")
        return None

    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        log.warning(
            f"Skipping file skill missing required name/description: {skill_md_path}"
        )
        return None

    return FileSkill(
        name=name,
        description=description,
        content=body,
        path=skill_md_path.parent,
        skill_md_path=skill_md_path,
    )


def discover_file_skills(skills_dir: Path) -> list[FileSkill]:
    root = skills_dir.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []

    cache_key = str(root)
    with _CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and _cache_is_fresh(cached):
            return list(cached["skills"])

    skills, directories = _scan_file_skills(root)
    cache_entry = {
        "skills": skills,
        "directories": _snapshot_paths(directories),
        "files": _snapshot_paths([skill.skill_md_path for skill in skills]),
    }

    with _CACHE_LOCK:
        _DISCOVERY_CACHE[cache_key] = cache_entry

    return list(skills)


def get_file_skill_by_name(name: str) -> Optional[FileSkill]:
    for skill in discover_file_skills(get_file_skills_dir()):
        if skill.name == name:
            return skill
    return None


def _split_frontmatter(content: str) -> Optional[tuple[str, str]]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    frontmatter_lines: list[str] = []
    closing_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = idx
            break
        frontmatter_lines.append(line)

    if closing_index is None:
        return None

    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return "\n".join(frontmatter_lines), body


def _scan_file_skills(root: Path) -> tuple[list[FileSkill], list[Path]]:
    directories: list[Path] = []
    skills: list[FileSkill] = []
    seen_names: set[str] = set()
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack and len(directories) < MAX_FILE_SKILL_DIRS:
        current_dir, depth = stack.pop()
        if depth > MAX_FILE_SKILL_DEPTH or not current_dir.is_dir():
            continue

        directories.append(current_dir)
        try:
            entries = sorted(current_dir.iterdir(), key=lambda item: item.name)
        except OSError as e:
            log.warning(f"Failed to scan file skills directory {current_dir}: {e}")
            continue

        for entry in entries:
            if entry.is_file() and entry.name == "SKILL.md":
                skill = parse_skill_md(entry)
                if skill is None:
                    continue
                if skill.name in seen_names:
                    log.warning(f"Skipping duplicate file skill name '{skill.name}' at {entry}")
                    continue
                seen_names.add(skill.name)
                skills.append(skill)
            elif (
                entry.is_dir()
                and depth < MAX_FILE_SKILL_DEPTH
                and not entry.name.startswith(".")
            ):
                stack.append((entry, depth + 1))

    skills.sort(key=lambda skill: (skill.name.lower(), str(skill.skill_md_path)))
    return skills, directories


def _snapshot_paths(paths: list[Path]) -> dict[str, int]:
    snapshot = {}
    for path in paths:
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            snapshot[str(path)] = -1
    return snapshot


def _cache_is_fresh(cache_entry: dict) -> bool:
    for snapshot in (cache_entry.get("directories", {}), cache_entry.get("files", {})):
        for path_str, old_mtime in snapshot.items():
            try:
                new_mtime = Path(path_str).stat().st_mtime_ns
            except OSError:
                return False
            if new_mtime != old_mtime:
                return False
    return True
