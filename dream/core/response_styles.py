"""Response styles (DREAM-181): how Dream words its replies, chosen with behaviour.response_style.

A style is one short block in the system prompt, right after Dream's base rules. It is read when a session's prompt is
built, so a change applies to the next session (/new); the running session keeps its prompt, and with it the engine's
cached prefix. `default` adds nothing: Dream answers as it always has.
"""
from __future__ import annotations

from . import _glyphs

STYLES = ("default", "professional", "warm", "concise", "weasel-ee")
LABELS = {
    "default": "Default",
    "professional": "Professional",
    "warm": "Warm and Inspiring",
    "concise": "Short and Concise",
    "weasel-ee": "Weasel's Personal Style - EE",
}
SUMMARIES = {
    "default": "Dream's own voice, with no extra style.",
    "professional": "Precise, neutral and structured: the answer first, in complete sentences.",
    "warm": "Friendly and encouraging, in plain language, ending on the next good step.",
    "concise": "As short as the question allows: the answer in a sentence or two.",
    "weasel-ee": "Weasel's own. Look closer.",
}

_PLAIN = {
    "professional": (
        "Answer in a professional, precise register. Lead with the answer. Use complete sentences and a neutral "
        "tone; structure longer replies with short headings or bullets where they help the reader. No slang, emoji "
        "or filler, and no restating the request."),
    "warm": (
        "Answer warmly and encouragingly, in plain, friendly language. Lead with the answer. Acknowledge real "
        "progress sincerely, never with empty praise, and end on the next good step. Keep it clear and concrete."),
    "concise": (
        "Answer as briefly as the question allows. Lead with the answer in one or two sentences; use bullets only "
        "for real lists. No preamble, no recap and no closing offers. Keep every fact, risk or step that would "
        "change a decision."),
}

# What any view of the styles shows for the last one. The real text is not these.
WEASEL_FACTS = (
    "The War Dance: when hunting, stoats perform a frantic, zigzagging \"war dance\" of leaps and twists that seems to "
    "bewilder their prey.",
    "The least weasel is the smallest member of the order Carnivora.",
    "In snowy places the stoat, a kind of weasel, turns white for winter; that white coat is called ermine.",
    "A weasel's long, thin body loses heat quickly, so it has to eat often.",
    "Least weasels are slim enough to chase voles and mice down their own burrows.",
    "Weasels belong to the mustelid family, along with otters, badgers, ferrets and wolverines.",
    "Ferrets are domesticated polecats, close cousins of the weasel.",
    "Like other mustelids, weasels have scent glands and use them to mark their territory.",
    "\"Pop Goes the Weasel\" was a popular English song and dance in the 1850s.",
    "Stoats regularly take rabbits several times their own weight.",
)


def label(style: str) -> str:
    return LABELS.get(style, style)


def shown_text(style: str) -> str:
    """What the settings views show as a style's text."""
    if style == "weasel-ee":
        return "\n".join(f"- {fact}" for fact in WEASEL_FACTS)
    return _PLAIN.get(style, "")


def prompt_section(style: str | None) -> str:
    """The style's block for the system prompt; "" for default or an unknown name."""
    if style == "weasel-ee":
        return "\n" + _glyphs.render().strip() + "\n"
    text = _PLAIN.get(style or "")
    return f"\n## How to answer\n{text}\n" if text else ""
