"""The prompt's building paragraph (DREAM-233, batch A1, D9): BASE and COMPACT_BASE tell the model how to build
something the owner can see -- one self-contained index.html in a new folder, parts under ~200 lines, the SAME file
edited with str_replace_edit, done(path), never the page pasted into the chat -- and write_file's description says
an .html or .svg opens in the owner's preview panel. Pinned, with a size bound so the cache-friendly head stays
small. No model."""
from dream.core import system_prompt
from dream.tools.native import write_file

KEY_PHRASES = [
    "Building something the user can see",
    "one\nself-contained `index.html` in a new folder named for it (`coffee-shop/index.html`)",
    "it opens in a panel beside the chat on its own",
    "https CDN",                       # DREAM-234: the preview has the internet (https); never "no network"
    "no localStorage",
    "`write_file` in parts under ~200 lines",
    "the whole page\nwith its styles first, then the script with `str_replace_edit` before `</body>`",
    "SAME file with `str_replace_edit`, never a new name",
    "`done(path)`",
    "never paste the page into the chat",
]


def paragraph(text, lead="Building something the user can see"):
    start = text.index(lead)
    end = text.index("\n", text.index("paste the page into the chat", start))
    return text[start:end]


def test_base_carries_the_building_paragraph_within_its_size():
    para = paragraph(system_prompt.BASE)
    flat = para.replace("\\\n", "")   # the file's line continuations
    for phrase in KEY_PHRASES:
        assert phrase.replace("\n", " ") in flat, phrase
    assert "local files beside it are fine, ES modules included" in flat   # DREAM-238: the module graph is inlined
    assert "no network" not in flat
    assert "it reports console errors; fix and call again" in flat
    assert "two or three sentences" in flat
    assert len(flat) <= 1000, len(flat)   # about 230 tokens at four characters a token


def test_compact_base_carries_the_short_form_within_its_size():
    para = paragraph(system_prompt.COMPACT_BASE)
    flat = para.replace("\n", " ")
    for phrase in KEY_PHRASES:
        assert phrase.replace("\n", " ") in flat, phrase
    assert "local files (ES modules too) fine" in flat and "no network" not in flat
    assert len(flat) <= 600, len(flat)   # about 140 tokens


def test_the_paragraph_is_in_each_base_exactly_once():
    for text in (system_prompt.BASE, system_prompt.COMPACT_BASE):
        assert text.count("Building something the user can see") == 1


def test_write_file_says_a_page_opens_in_the_preview_panel():
    assert "An .html, .svg or .md you write opens in the user's preview panel." in write_file.description
