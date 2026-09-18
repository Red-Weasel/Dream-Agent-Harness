"""Find skill packages on disk, index them cheaply, open them on demand.

The shape is Claude Code's and Codex's: a directory whose ``SKILL.md`` opens with
YAML frontmatter carrying ``name`` and ``description``, followed by the procedure in
markdown, with supporting material in sibling files the body links to.

Three properties this loader has to preserve:

- **Progressive disclosure.** 54 installed skills cost 54 short lines in the system
  prompt, and nothing more until one is actually opened. The description is the only
  part carried in context, which is why a skill with a vague one is nearly useless.
- **The author keeps ownership.** Nothing is copied into Dream's store. A skill
  edited in ``~/.claude/skills`` is different on the next boot, which is the whole
  point of loading from disk instead of importing.
- **A broken skill costs its own line, not the boot.** Bad frontmatter, an
  unreadable file, a dangling symlink: skipped with a warning, exactly as
  ``tools/registry`` treats a half-written custom tool.

Symlinked skill directories are ordinary here — ``~/.codex/skills`` is largely
symlinks into ``~/.claude/skills`` — so discovery follows them. Reading a bundled
file does not: a path is only served if it resolves back inside the skill it claims
to belong to, or a crafted ``references/../../../.ssh/id_rsa`` would read anything
the user can.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import yaml

# Bound each description in search results and the curated wake index. External
# catalogs stay searchable without being copied into the model's wake context.
INDEX_LINE_CHARS = 150

# A SKILL.md is meant to be read into context, so a runaway one is a bug in the skill,
# not something to serve whole. Generous enough that no legitimate skill hits it.
BODY_MAX_CHARS = 120_000

# Bundled files are read on demand and can be large reference docs; same reasoning.
FILE_MAX_CHARS = 120_000

# How deep to look for a SKILL.md under a root. Skills normally sit one level down
# (<root>/<skill>/SKILL.md); plugin caches nest them a little deeper.
MAX_DEPTH = 4
HEADER_MAX_CHARS = 16384

_MANIFEST = "SKILL.md"


class SkillReadFailure(str):
    """A failed read, retaining the text API used by existing loader callers."""


@dataclass(frozen=True)
class FileSkill:
    """One installed skill package."""

    name: str
    description: str
    manifest: Path      # the SKILL.md itself
    root: Path          # the skill's own directory (manifest.parent)
    source: str         # which tree it came from, for provenance in the index
    capabilities: tuple[str, ...] = ()  # declared metadata, never permissions
    portable: bool | None = None
    version: str = ""
    provenance: str = "external-read-only"
    parent_extension: str | None = None
    curated: bool = False  # True only for a shipped Dream workflow path.

    @property
    def index_line(self) -> str:
        desc = " ".join(self.description.split())
        if len(desc) > INDEX_LINE_CHARS:
            desc = desc[: INDEX_LINE_CHARS - 1].rstrip() + "…"
        return f"{self.name} — {desc}" if desc else self.name


def _frontmatter(text: str) -> dict:
    """The YAML block between the leading ``---`` fences, or {}.

    Deliberately forgiving: a skill whose frontmatter does not parse still has a
    usable body, so the caller can fall back to the directory name rather than drop
    the skill. Only a mapping counts — a bare string or list is not frontmatter.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_header(manifest: Path) -> str:
    """Read frontmatter only. Discovery never reads the procedure or scripts."""
    with manifest.open(encoding="utf-8", errors="replace") as f:
        first = f.readline(HEADER_MAX_CHARS)
        if not first.startswith("---"):
            return first
        lines, length = [first], len(first)
        while length < HEADER_MAX_CHARS:
            line = f.readline(HEADER_MAX_CHARS - length)
            if not line:
                break
            lines.append(line)
            length += len(line)
            if line.strip() == "---":
                break
        return "".join(lines)


def _package_metadata(root: Path, name: str) -> tuple[dict, list[str]]:
    """Portable optional manifest.json. Capability declarations grant nothing."""
    path = root / "manifest.json"
    if not path.is_file():
        return {}, []
    try:
        with path.open(encoding="utf-8") as f:
            text = f.read(HEADER_MAX_CHARS + 1)
        if len(text) > HEADER_MAX_CHARS:
            raise ValueError("manifest exceeds size limit")
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != "dream-skill/v1":
            raise ValueError("expected dream-skill/v1 manifest")
        if data.get("name") != name:
            raise ValueError("manifest name must match SKILL.md")
        caps = data.get("capabilities", [])
        if not isinstance(caps, list) or len(caps) > 32 or any(not isinstance(c, str) or len(c) > 120 for c in caps):
            raise ValueError("capabilities must be at most 32 short strings")
        if type(data.get("portable", True)) is not bool or not isinstance(data.get("version", ""), str):
            raise ValueError("portable must be boolean and version must be text")
        warnings = []
        if any(k in data for k in ("hooks", "command", "scripts", "permissions", "enabled")):
            warnings.append(f"skill '{name}': executable/permission fields in manifest ignored; use explicit Dream hook settings")
        return data, warnings
    except (OSError, ValueError, UnicodeError) as e:
        return {}, [f"skill '{name}': invalid manifest.json ({e}); SKILL.md retained"]


def enabled(skill: FileSkill) -> bool:
    from ..extensions import extension_id, is_enabled

    return is_enabled(extension_id("skill", skill.name), parent=skill.parent_extension)


def _source_label(root: Path) -> str:
    """A human name for the tree a skill came from.

    Roots are almost always literally called ``skills``, and the segment above one is
    as often a version hash (``bd2122cb``, ``1.12.0``) as a name — so walk up until a
    segment actually identifies something, and drop the leading dot off ``.codex``.
    """
    for part in reversed(root.parts):
        p = part.lstrip(".")
        if not p or p in {"skills", "cache", "plugins"}:
            continue
        if all(c.isdigit() or c == "." for c in part):     # 1.12.0
            continue
        if len(p) >= 8 and all(c in "0123456789abcdef" for c in p.lower()):  # bd2122cb
            continue
        return p
    return root.name or str(root)


def _iter_manifests(root: Path, max_depth: int = MAX_DEPTH) -> Iterator[Path]:
    """Every SKILL.md under ``root``, following symlinks, bounded in depth.

    A skill never contains another skill, so descent stops at the first manifest on a
    branch — otherwise a plugin cache that vendors its own skills would report them
    twice under two different names.
    """
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    # Some imported packages use Skill.md; prefer canonical casing when both exist.
    manifest = next((p for p in entries if p.name == _MANIFEST and p.is_file()), None)
    if manifest is None:
        manifest = next((p for p in entries if p.name.lower() == 'skill.md' and p.is_file()), None)
    if manifest is not None:
        yield manifest
        return
    if max_depth <= 0:
        return
    for entry in entries:
        try:
            if entry.is_dir():  # follows symlinks, which is what we want here
                yield from _iter_manifests(entry, max_depth - 1)
        except OSError:
            continue


def discover(dirs: Iterable[Path | str]) -> tuple[list[FileSkill], list[str]]:
    """Load every skill under ``dirs``. Returns (skills, warnings).

    Earlier directories win a name collision: the roots are a precedence list, so a
    local override placed first genuinely overrides the packaged copy rather than
    fighting it. Results are sorted by name so the index is stable across boots.
    """
    found: dict[str, FileSkill] = {}
    warnings: list[str] = []
    seen_roots: set[Path] = set()

    for raw in dirs:
        root = Path(raw).expanduser()
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        source = _source_label(root)

        for manifest in _iter_manifests(root):
            try:
                skill_root = manifest.parent.resolve()
            except OSError:
                continue
            # The same skill reached twice (a symlink tree pointing at a real one)
            # is one skill, and the first spelling of it is the one we keep.
            if skill_root in seen_roots:
                continue
            try:
                text = _read_header(manifest)
            except OSError as e:
                warnings.append(f"skill '{manifest.parent.name}' unreadable: {e}")
                continue
            meta = _frontmatter(text)
            if text.startswith("---") and not meta:
                # Almost always an unquoted ':' or '#' inside a value: YAML reads it
                # as structure and the whole block is lost. Worth naming precisely —
                # "no description" sends the author to rewrite prose that is fine.
                warnings.append(
                    f"skill at {manifest.parent} has frontmatter that does not parse "
                    f"(often an unquoted ':' in the description) — falling back to the "
                    f"directory name, with no description")
            name = str(meta.get("name") or manifest.parent.name).strip()
            if not name:
                warnings.append(f"skill at {manifest.parent} has no usable name — skipped")
                continue
            desc = str(meta.get("description") or "").strip()
            if not desc:
                warnings.append(f"skill '{name}' has no description — it will be hard to find")
            key = name.casefold()
            if key in found:
                warnings.append(
                    f"skill '{name}' at {manifest.parent} shadowed by "
                    f"{found[key].root} — first root wins"
                )
                continue
            seen_roots.add(skill_root)
            package, notes = _package_metadata(manifest.parent, name)
            warnings.extend(notes)
            if meta.get("hooks") or meta.get("permissions"):
                warnings.append(f"skill '{name}': hook/permission instructions are metadata only; no automatic execution")
            from .. import config, plugins
            from ..extensions import extension_id

            plugin = plugins.owner(manifest)
            provenance = "dream-plugin" if plugin else "dream-owned" if skill_root.is_relative_to(config.ROOT) else "external-read-only"
            found[key] = FileSkill(
                name=name, description=desc, manifest=manifest,
                root=manifest.parent, source=source,
                capabilities=tuple(package.get("capabilities", ())), portable=package.get("portable"),
                version=package.get("version", ""), provenance=provenance,
                parent_extension=extension_id("plugin", plugin.name) if plugin else None,
                curated=skill_root in {p.resolve() for p in config.bundled_skill_dirs()},
            )
    return [found[k] for k in sorted(found)], warnings


def index_lines(skills: Iterable[FileSkill]) -> list[str]:
    """One bounded line per skill — what the system prompt carries."""
    return [s.index_line for s in skills if enabled(s)]


def find(skills: Iterable[FileSkill], query: str) -> list[FileSkill]:
    """Skills whose name or description matches every whitespace-separated term.

    Substring, case-insensitive, AND across terms. Not clever on purpose: the index
    is already in context, so this exists to narrow a long list, not to rank it.
    """
    skills = [s for s in skills if enabled(s)]
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return list(skills)
    out = []
    for s in skills:
        hay = f"{s.name} {s.description}".lower()
        if all(t in hay for t in terms):
            out.append(s)
    return out


def load_body(skill: FileSkill) -> str:
    """The full SKILL.md, plus an inventory of what is bundled beside it.

    The inventory is the point of listing rather than inlining: the body's own links
    (``references/foo.md``) become fetchable names without spending context on files
    the task may not need.
    """
    if not enabled(skill):
        return SkillReadFailure(f"[skill '{skill.name}' is disabled in Dream settings]")
    try:
        with skill.manifest.open(encoding="utf-8", errors="replace") as f:
            text = f.read(BODY_MAX_CHARS + 1)
    except OSError as e:
        return SkillReadFailure(f"[skill '{skill.name}' could not be read: {e}]")
    if len(text) > BODY_MAX_CHARS:
        text = text[:BODY_MAX_CHARS] + f"\n\n[…truncated at {BODY_MAX_CHARS} chars]"
    extras = sorted(bundled_names(skill))
    if extras:
        text += (
            "\n\n---\nBundled with this skill (read one with skill_file):\n"
            + "\n".join(f"- {p}" for p in extras)
        )
    from ..extensions import extension_id, record_usage

    record_usage(extension_id("skill", skill.name), "opened")
    if skill.parent_extension:
        record_usage(skill.parent_extension, "opened")
    return text


def bundled_names(skill: FileSkill, limit: int = 200) -> list[str]:
    """Relative paths of the files bundled with a skill, excluding its manifest."""
    out: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(skill.root, followlinks=False):
            dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                if p == skill.manifest:
                    continue
                out.append(str(p.relative_to(skill.root)))
                if len(out) >= limit:
                    return out
    except OSError:
        pass
    return out


def bundled_file(skill: FileSkill, relpath: str) -> str:
    """Read one file bundled with ``skill``.

    ``relpath`` is attacker-shaped input: it reaches here from whatever the model
    emitted, and the model read it out of a SKILL.md that Dream did not write. So the
    path is resolved and required to land inside the skill's own resolved directory —
    ``../`` and absolute paths are refused by that check rather than by inspecting the
    string, which is the part that is easy to get wrong.
    """
    if not enabled(skill):
        return SkillReadFailure(f"[skill '{skill.name}' is disabled in Dream settings]")
    root = skill.root.resolve()
    try:
        target = (root / relpath).resolve()
    except OSError as e:
        return SkillReadFailure(f"[cannot resolve '{relpath}': {e}]")
    if target != root and root not in target.parents:
        return SkillReadFailure(f"[refused: '{relpath}' resolves outside the '{skill.name}' skill]")
    if not target.is_file():
        return SkillReadFailure(f"[no file '{relpath}' in the '{skill.name}' skill]")
    try:
        with target.open(encoding="utf-8", errors="replace") as f:
            text = f.read(FILE_MAX_CHARS + 1)
    except OSError as e:
        return SkillReadFailure(f"[could not read '{relpath}': {e}]")
    if len(text) > FILE_MAX_CHARS:
        text = text[:FILE_MAX_CHARS] + f"\n\n[…truncated at {FILE_MAX_CHARS} chars]"
    from ..extensions import extension_id, record_usage

    record_usage(extension_id("skill", skill.name), "file_read")
    if skill.parent_extension:
        record_usage(skill.parent_extension, "file_read")
    return text
