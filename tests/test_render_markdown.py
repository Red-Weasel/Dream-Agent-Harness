"""Dream's answers show what the markdown means, not its symbols.

The owner, 2026-10-09: [feedback]. An answer was printed as typed (`## Plan`, `**bold**`, `| a | b |`),
coloured but with every marker left in. The text between the markers is still the model's, character for character;
a fenced block and anything that only looks like markup stay exactly as written.
"""
from rich.console import Console

from dream.tui.render import Renderer

WIDTH = 76


def shown(answer, width=WIDTH, chunk=7):
    console = Console(record=True, force_terminal=True, width=width)
    r = Renderer(console)
    r._live = object()                      # the streaming path of a real turn
    for start in range(0, len(answer), chunk):
        r.assistant_delta(answer[start:start + chunk])
    r.end_line()
    return [line.rstrip() for line in console.export_text().rstrip("\n").split("\n")]


def test_headings_emphasis_and_code_lose_their_markers():
    out = shown("## Plan\n\nUse **two cards** and `--spec`, *not* one.\n")
    assert out == ["", "● Plan", "", "  Use two cards and --spec, not one."]


def test_list_items_get_a_bullet_and_keep_their_depth():
    out = shown("Steps:\n- first\n  - nested\n* second\n1. numbered\n- [ ] open\n- [x] done\n")
    assert out[1:] == ["● Steps:", "  • first", "    • nested", "  • second", "  1. numbered", "  ☐ open", "  ☑ done"]


def test_text_that_only_looks_like_markup_is_left_alone():
    line = 'Keep snake_case_name, 2 * 3 * 4, [red]literal[/red], a_b*c and print("[ok]") as typed.'
    assert "".join(shown(line + "\n", width=200)[1:]) == "● " + line


def test_a_fenced_block_is_literal_and_its_fences_are_not_shown():
    out = shown("Run:\n```bash\n# not a heading\necho **not bold** | cat\n```\nDone.\n")
    assert out[1:] == ["● Run:", "  # not a heading", "  echo **not bold** | cat", "  Done."]


def test_a_message_that_opens_with_a_fence_still_gets_its_bullet():
    out = shown("```\nfirst\n```\n")
    assert out[1:] == ["● first"]


def test_a_table_is_laid_out_in_columns():
    out = shown("Speeds:\n\n| Model | Reading | Writing |\n|---|---:|---:|\n| 27B | 2,962 | 66.6 |\n| **35B** | 3,001 | 417 |\n\nEnd.\n")
    rows = [line for line in out if "27B" in line or "35B" in line or "Model" in line]
    assert len(rows) == 3 and not any("|" in line or "*" in line for line in rows)
    assert len({line.index(word) for line, word in zip(rows, ("Reading", "2,962", "3,001"))}) <= 2   # one column
    assert all(line.startswith("  ") for line in rows) and out[-1] == "  End."
    assert not any(set(line) <= set("|-: ") and "-" in line for line in out)      # the |---| row is not printed


def test_a_table_wider_than_the_pane_stays_inside_it():
    out = shown("| Step | What happens |\n|---|---|\n| one | " + "a long explanation " * 12 + "|\n", width=50)
    assert all(len(line) <= 50 for line in out)
    assert " ".join(" ".join(out).split()).count("a long explanation") == 12     # every word, wrapped inside its column
    assert all(line.startswith("         ") for line in out[3:])                   # ... and under its own column


def test_links_quotes_and_rules():
    out = shown("See [the paper](https://arxiv.org/abs/2609.37725).\n> quoted words\n\n---\nAfter.\n")
    assert out[1] == "● See the paper (https://arxiv.org/abs/2609.37725)."
    assert out[2] == "  ▎ quoted words"
    assert set(out[4].strip()) == {"─"} and len(out[4].strip()) < WIDTH // 2
    assert out[5] == "  After."


def test_the_result_does_not_depend_on_how_the_stream_was_cut():
    answer = "## Findings\n- Keep `values[0]` and **this**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx = 1\n```\n"
    assert shown(answer, chunk=1) == shown(answer, chunk=3) == shown(answer, chunk=10000)


def test_wrapped_text_stays_inside_the_pane_and_under_itself():
    out = shown("- " + "**bold words** and plain words " * 6 + "\n", width=40)
    body = [line for line in out if line.strip()]
    assert len(body) > 2 and body[0].startswith("● • ")
    assert all(line.startswith("    ") and len(line) <= 40 for line in body[1:])
    assert "*" not in "".join(body)
