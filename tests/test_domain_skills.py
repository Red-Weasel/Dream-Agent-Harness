"""DREAM-172 domain skills: format, real Dream tool names that their presets keep, working links, the owner's
disclaimers and scope lines, and no near-duplicate text of the installed third-party skill packs (word 8-gram overlap;
DREAM_SKILL_OVERLAP_ROOTS, default ~/.claude/plugins and ~/.claude/skills, skipped where none exist)."""
import json
import os
import re
from pathlib import Path

import pytest

from dream import config, presets
from dream.skills import loader
from dream.tools import installed_skill_tools as ist, registry
from dream.tools.capability_tools import CAPABILITY_TOOLS
from dream.tools.demonstration_tools import DEMONSTRATION_TOOLS
from dream.tools.moe_tools import moe_tools
from dream.tools.native import NATIVE_TOOLS

ROOT = Path(__file__).resolve().parents[1] / "skills" / "domains"
TOOLS = list(registry._BASE_TOOLS) + list(NATIVE_TOOLS) + DEMONSTRATION_TOOLS + CAPABILITY_TOOLS + list(moe_tools())
BUILTIN = json.loads(presets.BUILTIN_PATH.read_text())["presets"]
SKILLS, WARNINGS = loader.discover([ROOT])


def test_every_domain_skill_is_well_formed_and_owned_by_a_preset():
    assert not WARNINGS and len(SKILLS) == len(list(ROOT.iterdir())) == 34
    listed = {name for p in BUILTIN.values() for name in p["skills"]}
    assert listed <= {s.name for s in SKILLS} | set(config.CURATED_SKILLS)
    for skill in SKILLS:
        text = skill.manifest.read_text(encoding="utf-8")
        assert skill.root.name == skill.name and not skill.curated and skill.provenance == "dream-owned", skill.name
        assert 0 < len(skill.description) <= 150 and 1500 <= len(text) <= 3500, skill.name
        assert all(f"\n## {h}\n" in text for h in ("Method", "Output", "Checks", "Pitfalls")), skill.name
        assert skill.name in listed, skill.name
        for link in re.findall(r"\]\(([^)]+)\)", text):
            assert link.startswith("https://") or (skill.root / link).is_file(), (skill.name, link)


def test_the_tools_named_are_real_and_kept_by_every_preset_that_lists_the_skill():
    groups = {t.name: presets.group(t) for t in TOOLS}
    curated = loader.discover(config.bundled_skill_dirs())[0]
    for skill in SKILLS + curated:
        text = skill.manifest.read_text(encoding="utf-8")
        named = {t for t in re.findall(r"`([a-z][a-z0-9_]+)`", text) if "_" in t or t in groups}
        if skill.curated:
            named &= set(groups)                    # curated texts also quote argument names such as `page_count`
        assert named <= set(groups), (skill.name, named - set(groups))
        if not skill.curated:
            assert set(skill.capabilities) == named, skill.name              # the manifest declares what it uses
        needs = (set(skill.capabilities) & set(groups)) | named              # declared and mentioned (DREAM-174)
        for preset in (p for p in BUILTIN.values() if skill.name in p["skills"]):
            kept = presets.CORE_TOOLS | {n for n, g in groups.items() if g in preset["tools"] or n in preset["tools"]}
            assert needs <= kept, (skill.name, needs - kept)


def test_every_built_in_preset_lists_all_its_skills_in_the_wake_index(monkeypatch):
    monkeypatch.setattr(ist, "_CACHE", None)
    installed = {s.name for s in ist.inventory(refresh=True)}
    for name, preset in BUILTIN.items():
        monkeypatch.setattr(presets, "_SESSION", presets._normal(name, preset))
        listed = {line.split(" — ")[0] for line in ist.index_lines()}
        assert set(preset["skills"]) & installed <= listed, (name, set(preset["skills"]) & installed - listed)


def test_legal_finance_and_security_carry_their_lines():
    lines = {"Legal": "not legal advice", "Finance": "not financial, tax or investment advice",
             "Security": "Defensive use only"}
    for preset, line in lines.items():
        for name in BUILTIN[preset]["skills"]:
            if (ROOT / name).is_dir():
                assert line in (ROOT / name / "SKILL.md").read_text(), name


def test_default_searches_them_but_lists_only_the_curated_index(monkeypatch):
    monkeypatch.setattr(presets, "_SESSION", None)
    monkeypatch.setattr(ist, "_CACHE", None)
    names = {s.name for s in ist.installed(refresh=True)}
    assert {s.name for s in SKILLS} <= names
    assert not any(line.startswith("contract-review") for line in ist.index_lines())


def _shingles(text: str, n: int = 8) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def test_no_near_duplicate_of_the_installed_third_party_skill_packs():
    env = os.environ.get("DREAM_SKILL_OVERLAP_ROOTS")
    roots = [Path(p) for p in env.split(os.pathsep)] if env else [Path.home() / ".claude" / "plugins",
                                                                     Path.home() / ".claude" / "skills"]
    files = [f for r in roots if r.is_dir() for f in r.rglob("*.md") if f.is_file() and f.stat().st_size < 200_000]
    if not files:
        pytest.skip("no third-party skill packs to compare against (set DREAM_SKILL_OVERLAP_ROOTS)")
    reference: dict[tuple[str, ...], Path] = {}
    for f in files:
        for gram in _shingles(f.read_text(encoding="utf-8", errors="replace")):
            reference.setdefault(gram, f)
    for skill in SKILLS:
        shared = [g for g in _shingles(skill.manifest.read_text(encoding="utf-8")) if g in reference]
        assert len(shared) < 2, (skill.name, [(" ".join(g), str(reference[g])) for g in shared[:5]])
