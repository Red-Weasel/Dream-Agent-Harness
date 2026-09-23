"""Loading skill packages off disk.

The loader's job is to make 54 installed skills cost 54 lines of context and nothing
more, without letting a malformed one take the boot down or a crafted reference path
read outside the skill it claims to belong to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.skills import loader


def _skill(root: Path, name: str, desc: str = "", body: str = "body") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n" if desc else \
         f"---\nname: {name}\n---\n\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")
    return d


# --- discovery ---------------------------------------------------------------


def test_a_directory_with_a_manifest_is_a_skill(tmp_path):
    _skill(tmp_path, "alpha", "Use when alpha things happen")
    skills, warnings = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["alpha"]
    assert skills[0].description == "Use when alpha things happen"
    assert not warnings


def test_frontmatter_name_wins_over_the_directory_name(tmp_path):
    d = tmp_path / "on-disk-dirname"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: real-name\ndescription: d\n---\nbody\n",
                                encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["real-name"]


def test_a_skill_without_frontmatter_still_loads_under_its_directory_name(tmp_path):
    d = tmp_path / "bare"
    d.mkdir()
    (d / "SKILL.md").write_text("# Just a heading, no frontmatter\n", encoding="utf-8")
    skills, warnings = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["bare"]
    assert any("no description" in w for w in warnings)


def test_unparseable_frontmatter_does_not_lose_the_skill(tmp_path):
    """A skill with broken YAML still has a usable body — dropping it would be worse
    than falling back to the directory name."""
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["broken"]


def test_discovery_follows_symlinked_skill_directories(tmp_path):
    """~/.codex/skills is largely symlinks into ~/.claude/skills."""
    real = tmp_path / "real"
    real.mkdir()
    _skill(real, "shared", "a shared skill")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "shared").symlink_to(real / "shared")
    skills, _ = loader.discover([linked])
    assert [s.name for s in skills] == ["shared"]


def test_the_same_skill_reached_twice_is_reported_once(tmp_path):
    """A real tree and a symlink tree pointing at it are one skill, not two."""
    real = tmp_path / "real"
    real.mkdir()
    _skill(real, "shared", "a shared skill")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "shared").symlink_to(real / "shared")
    skills, _ = loader.discover([real, linked])
    assert [s.name for s in skills] == ["shared"]


def test_earlier_roots_win_a_name_collision(tmp_path):
    """The roots are a precedence list, so an override placed first really overrides."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    _skill(a, "dup", "the override")
    _skill(b, "dup", "the packaged copy")
    skills, warnings = loader.discover([a, b])
    assert len(skills) == 1
    assert skills[0].description == "the override"
    assert any("shadowed" in w for w in warnings)


def test_a_missing_root_is_skipped_not_fatal(tmp_path):
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path / "does-not-exist", tmp_path])
    assert [s.name for s in skills] == ["alpha"]


def test_descent_stops_at_the_first_manifest(tmp_path):
    """A skill never contains another skill; a vendored copy inside one must not be
    reported as a second, differently-named skill."""
    outer = _skill(tmp_path, "outer", "d")
    _skill(outer, "vendored-inner", "d")
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["outer"]


def test_results_are_sorted_so_the_index_is_stable(tmp_path):
    for n in ("zulu", "alpha", "mike"):
        _skill(tmp_path, n, "d")
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["alpha", "mike", "zulu"]


# --- the index ---------------------------------------------------------------


def test_every_skill_gets_a_line_even_when_there_are_many(tmp_path):
    """Truncating the LIST would hide skills; only descriptions get shortened."""
    for i in range(60):
        _skill(tmp_path, f"skill-{i:02d}", "d")
    skills, _ = loader.discover([tmp_path])
    assert len(loader.index_lines(skills)) == 60


def test_a_long_description_is_clipped_to_one_bounded_line(tmp_path):
    _skill(tmp_path, "verbose", "x" * 900)
    skills, _ = loader.discover([tmp_path])
    line = loader.index_lines(skills)[0]
    assert len(line) <= loader.INDEX_LINE_CHARS + len("verbose — ") + 1
    assert line.endswith("…")


def test_a_multiline_description_collapses_to_one_line(tmp_path):
    d = tmp_path / "folded"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: folded\ndescription: >\n  first part\n  second part\n---\nbody\n",
        encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    assert "\n" not in loader.index_lines(skills)[0]
    assert "first part second part" in skills[0].description


# --- search ------------------------------------------------------------------


def test_find_matches_all_terms_across_name_and_description(tmp_path):
    _skill(tmp_path, "debugging", "Use when a test fails unexpectedly")
    _skill(tmp_path, "deploying", "Use when shipping to production")
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in loader.find(skills, "test fails")] == ["debugging"]
    assert [s.name for s in loader.find(skills, "deploying")] == ["deploying"]
    assert loader.find(skills, "test production") == []


def test_an_empty_query_returns_everything(tmp_path):
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    assert len(loader.find(skills, "   ")) == 1


# --- opening a skill ---------------------------------------------------------


def test_load_body_returns_the_manifest_text(tmp_path):
    _skill(tmp_path, "alpha", "d", body="## Steps\n1. do the thing")
    skills, _ = loader.discover([tmp_path])
    assert "1. do the thing" in loader.load_body(skills[0])


def test_load_body_lists_what_is_bundled_beside_the_manifest(tmp_path):
    d = _skill(tmp_path, "alpha", "d")
    (d / "references").mkdir()
    (d / "references" / "deep.md").write_text("detail", encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    body = loader.load_body(skills[0])
    assert "references/deep.md" in body
    assert "detail" not in body  # listed, not inlined — that is the whole point


def test_bundled_listing_excludes_the_manifest_itself(tmp_path):
    d = _skill(tmp_path, "alpha", "d")
    (d / "notes.md").write_text("n", encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    assert loader.bundled_names(skills[0]) == ["notes.md"]


def test_an_oversized_manifest_is_truncated_rather_than_served_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "BODY_MAX_CHARS", 100)
    _skill(tmp_path, "huge", "d", body="y" * 5000)
    skills, _ = loader.discover([tmp_path])
    body = loader.load_body(skills[0])
    assert "truncated" in body
    assert len(body) < 1000


# --- reading a bundled file, and refusing to read anything else --------------


def test_a_bundled_reference_can_be_read(tmp_path):
    d = _skill(tmp_path, "alpha", "d")
    (d / "references").mkdir()
    (d / "references" / "deep.md").write_text("the detail", encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    assert loader.bundled_file(skills[0], "references/deep.md") == "the detail"


def test_a_traversal_path_is_refused(tmp_path):
    """`relpath` comes from a SKILL.md Dream did not write, by way of the model."""
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], "../secret.txt")
    assert "refused" in out
    assert "private" not in out


def test_a_deep_traversal_path_is_refused(tmp_path):
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], "references/../../../../etc/passwd")
    assert "refused" in out
    assert "root:" not in out


def test_an_absolute_path_is_refused(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], str(secret))
    assert "refused" in out
    assert "private" not in out


def test_a_symlink_escaping_the_skill_is_refused(tmp_path):
    """Containment is decided on the RESOLVED path, so a symlink planted inside the
    skill cannot be used to step outside it."""
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    d = _skill(tmp_path, "alpha", "d")
    (d / "escape.txt").symlink_to(secret)
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], "escape.txt")
    assert "refused" in out
    assert "private" not in out


def test_a_missing_bundled_file_says_so(tmp_path):
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    assert "no file" in loader.bundled_file(skills[0], "references/absent.md")


def test_a_directory_is_not_a_readable_file(tmp_path):
    d = _skill(tmp_path, "alpha", "d")
    (d / "references").mkdir()
    skills, _ = loader.discover([tmp_path])
    assert "no file" in loader.bundled_file(skills[0], "references")


@pytest.mark.parametrize("nasty", ["", ".", "..", "./../"])
def test_odd_paths_never_escape(tmp_path, nasty):
    _skill(tmp_path, "alpha", "d")
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], nasty)
    assert "refused" in out or "no file" in out


def test_unparseable_frontmatter_is_named_as_such(tmp_path):
    """An unquoted ':' in a description makes YAML read it as structure and lose the
    whole block. Reporting that as "no description" sends the author to rewrite prose
    that was never the problem — caught this exact way while writing a skill here."""
    d = tmp_path / "colon"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: colon\ndescription: Covers this: and that\n---\nbody\n",
        encoding="utf-8")
    skills, warnings = loader.discover([tmp_path])
    assert [s.name for s in skills] == ["colon"]          # not lost
    assert any("does not parse" in w for w in warnings)
    assert any("unquoted ':'" in w for w in warnings)


def test_a_quoted_colon_in_a_description_parses_fine(tmp_path):
    d = tmp_path / "quoted"
    d.mkdir()
    (d / "SKILL.md").write_text(
        '---\nname: quoted\ndescription: "Covers this: and that"\n---\nbody\n',
        encoding="utf-8")
    skills, warnings = loader.discover([tmp_path])
    assert skills[0].description == "Covers this: and that"
    assert not warnings


def test_a_missing_bundled_file_lists_what_the_skill_has(tmp_path):
    """2026-09-23 (DREAM-091 follow-up): the model invented two paths in a row and only "no file" came back."""
    d = _skill(tmp_path, "alpha", "d")
    (d / "references").mkdir()
    (d / "references" / "deep.md").write_text("the detail", encoding="utf-8")
    (d / "scripts").mkdir()
    (d / "scripts" / "run.mjs").write_text("// script", encoding="utf-8")
    skills, _ = loader.discover([tmp_path])
    out = loader.bundled_file(skills[0], "references/absent.md")
    assert "no file 'references/absent.md'" in out
    assert "references/deep.md" in out and "scripts/run.mjs" in out
