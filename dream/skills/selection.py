"""Small deterministic task workflows. No inference or external catalog expansion."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from . import loader

MAX_GUIDANCE_CHARS = 4000     # the default budget: a short workflow, as for a small window
# DREAM-101: a session with a large window gets the whole workflow of the skill it asked for instead of a 2,200-char
# summary and an errand to skill_open -- 15 % of the window in chars (4 chars per token), never above the loader's body cap
GUIDANCE_WINDOW_SHARE = 0.15
CHARS_PER_TOKEN = 4


def guidance_budget(window_tokens: int | None) -> int:
    """Guidance chars for a session whose model window is `window_tokens` (None/unknown = the short default)."""
    if not window_tokens or window_tokens <= 0:
        return MAX_GUIDANCE_CHARS
    return max(MAX_GUIDANCE_CHARS, min(loader.BODY_MAX_CHARS, int(window_tokens * CHARS_PER_TOKEN * GUIDANCE_WINDOW_SHARE)))
MAX_WORKFLOWS = 2


@dataclass(frozen=True)
class TaskGuidance:
    text: str = ''
    names: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# Match concrete work, rather than broad words such as "help", "make" or "file".
# Multiple independent clues strengthen a match; explicit skill requests come first.
_PAINT_APP = r'(?:JS Paint|Microsoft Paint|Paint|Krita|GIMP|Photoshop|MyPaint)\b'
_DRAW_REQUEST = (r'(?:^|[.!?\n]\s*)(?:please\s+)?'
                 r'(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?'
                 r'(?:help me\s+|I want you to\s+)?')
_ART_OBJECT = r'(?:portrait|drawing|artwork|painting|sketch|image|photo|picture|illustration|canvas)\b'
# DREAM-103 (gate): the implicit rules of the three Understand skills are ALLOWLISTS. A missed implicit match is cheap --
# the owner can name the skill ($name, "use/run the X skill", /understand), and the dock's own request names it -- while
# a false one turns a bug fix into a map or domain build. So a rule looks only at the request's OPENING: its first words
# (after "hey/ok/okay/please," at most, then a connector, "can/could/would/will you" and "please"), or a clause joined
# to its first sentence by a comma and then/also/next/now/"and then". A sentence end (. ! ? ;), a colon, a new line or
# a list item ends the opening; what follows is description -- a quoted step, a stage list, a checklist,
# expected/actual -- and never selects. After the verb come the target phrase and then only an allowed follower
# ("of|for|on this repo", "for the Understand panel", "into a knowledge graph") and "now", "again" or "please" before
# the end or clause punctuation. A request that asks something never selects implicitly: it starts with a question word,
# or one of its sentences ends in "?" and has a question word at its head or after , ; :.
_ASKS = r'(?:how|what|why|when|where|which|who|whose|is|are|was|were|does|do|did|has|have)'
# linear: each sentence is checked once (the round-4 form rescanned to the sentence end from every , ; : -- 907 ms)
_NOT_ASKING = (r'^(?!\W*' + _ASKS + r'\b)(?![\s\S]*?(?:^|[.!?\n])(?=[^.!?\n]*\?)(?:\s*|[^.!?\n]*?[,;:]\s*)'
               + _ASKS + r'\b)')
# A request that reads as a bug report never selects one implicitly either: it can open with the very label or step it
# is about ("Map this repo. Nothing happens when I click it. Fix it."). Bug words and the usual symptoms both count.
_NOT_A_BUG_REPORT = (r"^(?![\s\S]*?\b(?:fix(?:es|ed|ing)?|bugs?|crash\w*|errors?|\w+Error|exceptions?|traceback|broken"
                     r"|fail(?:s|ed|ing|ure)?|doesn'?t\s+work|does\s+not\s+work|not\s+working|nothing\s+happens|does\s+nothing"
                     r'|repro\w*|regression|todo|fixme|expected\s*:|actual\s*:|hangs?|stuck'
                     r'|empty|blank|stale|missing|nothing|wrong|slow|forever|incorrect|disappear\w*|garbled|corrupt\w*'
                     r'|(?:0|zero)\s+nodes)\b)')
_OPENER = r'(?:(?:and\s+then|then|also|next|now|first|so|and)\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?'
# a slash command counts only at the start of the request or of a line, and never with a "/" after it (a path)
_SLASH = r'(?:^|\n)[ \t]*/'
_REQUEST = (_NOT_ASKING + _NOT_A_BUG_REPORT + r'\s*(?:(?:hey|ok|okay|please)\b\s*,?\s*)?' + _OPENER
            + r'(?:(?:[^.!?;:\n]|\.(?=\w))*?,\s*(?:and\s+then|then|also|next|now)\s+'
            r'(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?)?')
_REPO = r'(?:repo|repository|project|codebase|code\s?base|code)\b'
# What may follow the request is allowlisted too: only further INSTRUCTIONS, each opened by a mark (", then/also/next/
# now/and", " and (then)", a sentence end or a new line) and an optional connector / "can you" / "please" / "don't", then
# one of these verbs. "Map this repo. The dock is empty." is a report -- "The" opens no instruction -- and selects
# nothing. The clause text is possessive, so a long prompt is read once.
_INSTRUCTION = (r'(?:open|show|write|add|explain|summari[sz]e|list|map|build|generate|create|update|refresh|rebuild|run|use'
                r'|skip|exclude|include|ignore|keep|save|put|make|tell|let|report|focus|start|limit|leave|highlight|note|mark'
                r'|name|label|group|give|send|check|review|walk|describe|document|compare|find|search|look|read)\b')
_CONTINUE = (r'(?:(?:,\s*|\s+)(?:and\s+then|then|also|next|now|and)\s+|[.!?;]\s*|\s*\n\s*)'
             r'(?:(?:and\s+then|then|also|next|now|afterwards|finally|and)\s+)?(?:(?:can|could|would|will)\s+you\s+)?'
             r"(?:please\s+)?(?:(?:don'?t|do\s+not|never)\s+)?" + _INSTRUCTION + r'(?:[^.!?;\n]|\.(?=\w))*+')
# (no ":" after the target: a colon introduces a description, after the target as before it)
_FOLLOWER = (r'(?:\s+(?:of|for|on)\s+(?:this|the|my|our)\s+' + _REPO +
             r'|\s+for\s+the\s+understand(?:-anything)?\s+(?:panel|dock|dashboard)\b'
             r'|\s+into\s+(?:a|the)\s+knowledge[- ]graph\b)')
_TAIL = r'(?:\s*,?\s+(?:now|again|please))?'
_ALLOWED_END = (r'(?:' + _FOLLOWER + r'){0,2}' + _TAIL
                + r'(?:' + _CONTINUE + r')*[\s.!?;,]*$')   # (stray marks: _positive_actions blanks "skip X" / "don't X")
# Gate note 1: an UNQUALIFIED target ("a/the knowledge graph", "the business processes", "map the codebase") names no
# project of the owner's, and followed by coding sentences it is a coding request ("Create a knowledge graph. Use spaCy
# and store it in Neo4j."). It may go on only with ", then" and a verb that reads the map -- unless a follower
# qualifies it: "of/for/on this|my|our repo" ("the project" does not: the cheap direction).
_QUALIFIER = r'\s+(?:of|for|on)\s+(?:this|my|our)\s+' + _REPO
_UNQUALIFIED_END = (r'(?:(?:' + _FOLLOWER + r')?' + _QUALIFIER + r'(?:' + _FOLLOWER + r')?' + _TAIL
                    + r'(?:' + _CONTINUE + r')*|(?:' + _FOLLOWER + r'){0,2}' + _TAIL
                    + r'(?:,\s*then\s+(?:explain|open|show|tell|summari[sz]e)\b(?:[^.!?;\n]|\.(?=\w))*+)?)[\s.!?;,]*$')
_WHOLE_END = r'(?:\s+(?:now|again|please))?\s*[.!?]?\s*$'
_DRAW_ACTION = (r'(?:continue\s+)?(?:(?:draw(?:ing)?|paint(?:ing)?|repaint(?:ing)?|sketch(?:ing)?)\b|'
                r'(?:polish(?:ing)?|retouch(?:ing)?|edit(?:ing)?)\s+'
                r'(?:(?:my|our|this|the|an?|existing|saved|current|unfinished)\s+){0,4}'
                r'(?:' + _ART_OBJECT + r'|(?:highlights|shadows|edges|colors)\b[^.!?\n]{0,50}'
                + _ART_OBJECT + r'))')
_RULES = {
    'computer-use': (
        r'\b(?:use|operate|control)\b.{0,25}\b(?:browser|desktop)\b.{0,30}\b(?:to|interface|app)\b',
        r'\b(?:click|drag|scroll|select|type into)\b.{0,55}\b(?:button|dialog|menu|field|window|tab)\b',
        _DRAW_REQUEST + _DRAW_ACTION + r'[^.!?\n]{0,160}\b(?:in|using)\s+(?:the\s+)?' + _PAINT_APP,
        _DRAW_REQUEST + r'(?:in|using)\s+(?:the\s+)?' + _PAINT_APP + r'[, :\t]+(?:please\s+)?' + _DRAW_ACTION,
        _DRAW_REQUEST + r'use\s+(?:the\s+)?' + _PAINT_APP + r'\s+to\s+' + _DRAW_ACTION,
    ),
    'blender-animation': (
        r'\bblender\b|\.blend\b|\bbpy\b',
    ),
    'coding': (
        r'\b(?:debug|bug|traceback|stack trace|refactor|unit test|integration test|code review|codebase|repository|repo|pull request)\b',
        r'\b(?:python|javascript|typescript|react|html|css|sql|rust|function|component|api|website|web app|prototype|wireframe|design system)\b',
        # DREAM-103: "build a code map" asks for the Understand map, not for code (so an Understand match keeps it alone)
        r'\b(?:implement|fix|build|change|add|write|create)\b.{0,65}\b(?:code(?![\s-]+map\b)|script|test|endpoint|page|site|app|feature|button|form)\b',
    ),
    'research': (
        r'\b(?:research|look up|search the web|find sources|fact.check|verify sources|literature review|citations|current guidelines)\b',
        r'\b(?:latest|current|recent|today)\b.{0,60}\b(?:news|prices|standards|documentation|release|guidelines|version|rates)\b',
        r'\b(?:compare|recommend)\b.{0,65}\b(?:products|options|providers|tools|services|plans)\b',
    ),
    'writing': (
        r'\b(?:write|draft|rewrite|revise|polish|proofread|edit|shorten)\b.{0,65}\b(?:email|memo|report|essay|article|proposal|story|letter|copy|text|sentence|paragraph|post|brief|document|summary)\b',
        r'\b(?:tone|grammar|wording|copywriting|plain language|executive summary)\b',
    ),
    'documents': (
        r'\b(?:pdf|docx|pptx|powerpoint|word document|slide deck|slides|presentation)\b',
        r'\b(?:export|print|convert)\b.{0,60}\b(?:document|deck|page|html|file)\b',
    ),
    'data-analysis': (
        r'\b(?:csv|xlsx|spreadsheet|workbook|dataset|dataframe|pivot|tabular|tsv)\b',
        r'\b(?:analy[sz]e|calculate|aggregate|clean|chart|plot|sum|average)\b.{0,60}\b(?:data|sales|revenue|rows|columns|records|numbers|totals|expenses)\b',
    ),
    'media': (
        r'\b(?:video|animations?|animat(?:e[ds]?|ing)|motion graphic|storyboard|comfyui|blender|mp4|webm)\b',
        r'\b(?:generate|create|edit|crop|resize|render|export)\b.{0,60}\b(?:image|photo|picture|logo|visual|media|audio)\b',
    ),
    'library': (
        r'\b(?:my|the|your) library\b|\blibrary (?:file|document|version|folder|search|restore)\b',
        r'\b(?:restore|update|find|retrieve)\b.{0,60}\b(?:saved (?:plan|report|document)|previous version|last week.s (?:plan|notes))\b',
    ),
    'brainstorming': (
        r'\b(?:brainstorm(?:ing)?|spec (?:this|it) out|think (?:this |it )?through|design (?:how|the approach|a plan|the plan)|what approach|before (?:we|I|you) (?:build|implement|code|start)|plan (?:out|for) (?:the|a|this))\b',
        r'\b(?:requirements|trade-?offs?|options?|architecture)\b.{0,50}\b(?:decide|choose|recommend|weigh|compare)\b',
    ),
    'debugging': (
        r'\b(?:root cause|debug(?:ging)?|intermittent(?:ly)?|flaky|fails? only|reproduce|regression)\b',
        r'\bwhy (?:does|do|is|are|did) .{0,60}\b(?:fail|crash|break|hang|wrong|error)',
    ),
    'gated-build': (
        r'\b(?:gated build|alpha[ -]?omega|build (?:it |this )?in phases|phases? with (?:a |an )?(?:fresh |independent )?(?:verifier|evaluator|gate)|pass/fail gates?|checkpoint gates?)\b',
    ),
    'frontend-design': (
        r'\b(?:visual identity|typography|distinctive|aesthetic|look and feel|ui design|redesign|landing page|hero section|color palette|design tokens)\b',
    ),
    'grill-me': (
        r'\bgrill(?:ing)?\s+(?:me|us)\b|\bgrill\s+(?:my|our|this|the)\s+(?:plan|idea|design|decision|proposal|thinking|approach|spec|architecture)\b',
        r'\b(?:stress[- ]test|pressure[- ]test|poke holes in)\b.{0,40}\b(?:plan|idea|design|decision|proposal|thinking|approach|spec|architecture)\b',
    ),
    'handoff': (
        r'\bhand-?\s?off\b.{0,60}\b(?:session|conversation|agent|model|chat|context)\b|\b(?:session|conversation|agent|chat)\b.{0,60}\bhand-?\s?off\b',
        r'\bhand\s+(?:this|it|the)(?:\s+(?:work|session|conversation|task|chat))?\s+(?:off|over)\b',
    ),
    'verifying': (
        r'\b(?:verify|verification|validate|sanity.check|acceptance check|regression|recover|recovery|stuck|blocked|retry|interrupted|resume task)\b',
        r'\b(?:command|tool|export|operation)\b.{0,35}\b(?:failed|failure|error|timed out|timeout)\b',
    ),
    # DREAM-102: the repository map for the Understand panel; the dock's own request names the skill explicitly.
    # DREAM-103 (gate): an allowlist (see _REQUEST) -- "map (out) this repo / the whole project", "build / refresh ... the
    # (fresh) knowledge graph", "build / run ... the code / Understand-Anything map", "turn this repo into a knowledge
    # graph"; or the whole request is "understand map" or "knowledge graph of this project"; or /understand.
    'understand': (
        # qualified targets -- this/my/our repo, a code or Understand-Anything map -- may go on with further instructions
        _REQUEST + r'(?:(?:map\s+(?:out\s+)?(?:this|our|my)\s+(?:(?:whole|entire|current)\s+)?'
        r'(?:repo|repository|codebase|code\s?base|project|source tree|source code|code)\b'
        r'|(?:build|generate|create|make|produce|rebuild|regenerate|redo|refresh|update|run)\s+(?:(?:a|an|the|this|our|my)\s+)?'
        r'(?:(?:new|fresh|full|complete)\s+)?(?:understand(?:-anything)?|code|codebase|repo|repository|project)\s+map\b'
        r'|turn\s+(?:this|my|our)\s+' + _REPO + r'\s+into\s+(?:a|the)\s+knowledge[- ]graph\b)' + _ALLOWED_END
        # unqualified ones (gate note 1) only as _UNQUALIFIED_END allows
        + r'|(?:map\s+(?:out\s+)?(?:(?:the|that)\s+)?(?:(?:whole|entire|current)\s+)?'
        r'(?:repo|repository|codebase|code\s?base|project|source tree|source code|code)\b'
        r'|(?:build|generate|create|make|produce|rebuild|regenerate|redo|refresh|update)\s+(?:(?:a|an|the|this|our|my)\s+)?'
        r"(?:(?:new|fresh|full|complete)\s+)?(?:(?:project|repo|repository|codebase)['’]s\s+)?knowledge[- ]graph\b"
        r'|turn\s+(?:the|that)\s+' + _REPO + r'\s+into\s+(?:a|the)\s+knowledge[- ]graph\b)' + _UNQUALIFIED_END + r')',
        _NOT_ASKING + r'\W*(?:please\s+)?(?:(?:the|an?)\s+)?(?:understand(?:-anything)?\s+map'
        r'|knowledge[- ]graph\s+(?:of|for)\s+(?:this|the|my|our)\s+' + _REPO + r')' + _WHOLE_END,
        _SLASH + r'understand(?![\w/-])',
    ),
    # DREAM-103: looking at the map, an allowlist -- (i) a view verb and the Understand dashboard / panel / dock, the
    # knowledge graph dashboard or the map dashboard; (ii) the whole request is "open / show (me) the (understand) map";
    # or /understand-dashboard. "Open the map.ts file" and "the map editor" never select it.
    'understand-dashboard': (
        _REQUEST + r'(?:open|show(?:\s+me)?|view|display|see|bring\s+up|pull\s+up)\s+(?:the\s+)?'
        r'(?:understand(?:-anything)?\s+(?:dashboard|panel|dock)|knowledge[- ]graph\s+dashboard|map\s+dashboard)\b' + _ALLOWED_END,
        _NOT_ASKING + r'\W*(?:please\s+)?(?:open|show(?:\s+me)?)\s+(?:the\s+)?(?:understand(?:-anything)?\s+)?map'
        r'(?:\s+(?:now|again|please))?\s*[.!]?\s*$',
        _SLASH + r'understand-dashboard(?![\w/-])',
    ),
    # DREAM-103: the business domains, flows and steps, an allowlist -- a build verb, "(a|the) domain (graph|map)" or "the
    # business (domains|flows|processes)", an allowed follower; or /understand-domain. Not "the domain model": in a
    # domain-driven codebase updating or building it is ordinary coding work. Every domain target is unqualified until a
    # follower names this repo (gate note 1).
    'understand-domain': (
        _REQUEST + r'(?:build|generate|create|make|extract|map|produce|refresh|update|rebuild)\s+'
        r'(?:(?:a|the)\s+domain\s+(?:graph|map)|the\s+business\s+(?:domains|flows|processes))\b' + _UNQUALIFIED_END,
        _SLASH + r'understand-domain(?![\w/-])',
    ),
}
_PATTERNS = {name: tuple(re.compile(pattern, re.I) for pattern in patterns)
             for name, patterns in _RULES.items()}
# Action-bearing patterns outrank incidental format/language nouns when the
# two-workflow admission budget is contested. Explicit skill requests still win.
_ACTION_PATTERN_INDEX = {
    'computer-use': (0, 1, 2, 3, 4), 'coding': (2,), 'research': (0, 2),
    'writing': (0,), 'documents': (1,), 'data-analysis': (1,), 'media': (1,),
    'library': (1,), 'verifying': (1,), 'brainstorming': (0,), 'debugging': (0, 1),
    'gated-build': (0,), 'frontend-design': (0,), 'grill-me': (0, 1), 'handoff': (0, 1), 'understand': (0, 1, 2),
    'understand-dashboard': (0, 1, 2), 'understand-domain': (0, 1),
}
# A selected workflow that contradicts another drops it unless the user named it: grill-me asks every open decision
# per round, brainstorming one question per message (DREAM-090). A repository map (understand, DREAM-102) is a fixed
# tool pipeline; the coding workflow that "repo"/"build a code map" also wakes would only compete with it for the room.
_DISPLACES = {'grill-me': ('brainstorming',), 'understand': ('coding',)}
# DREAM-103 (gate): an Understand skill displaces coding only when coding matched on incidental words ("repo" in "map this
# repo"). When coding's own action pattern matched too, both load -- coding first on a tie -- so a misfire costs a
# second workflow, not the turn. The dashboard and domain skills displace nothing; the rule would hold for them too.
_DISPLACES_ONLY_INCIDENTAL = frozenset({'understand', 'understand-dashboard', 'understand-domain'})


def _request_text(prompt: str) -> str:
    """Exclude conventional quoted task data from deterministic routing.

    This is an admission heuristic, not a parser or an authorization boundary.
    Explicit quoted skill names and filenames remain usable as task arguments.
    """
    lines, fence = [], None
    for line in prompt.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            lines.append('\n')
        elif marker:
            fence = marker[1]
            lines.append('\n')
        elif re.match(r'^\s*>', line):
            lines.append('\n')
        else:
            lines.append(line)
    text = ''.join(lines)
    quoted = re.compile(r'"([^"\n]*)"|(?<!\w)\x27([^\x27\n]*)\x27(?!\w)|“([^”\n]*)”|‘([^’\n]*)’|`([^`\n]*)`')

    def keep_argument(match: re.Match) -> str:
        body = next(group for group in match.groups() if group is not None)
        filename = re.fullmatch(r'[^!?;\n]+\.(?:blend|pdf|docx|pptx|csv|xlsx|mp4|webm)', body, re.I)
        before = text[max(0, match.start() - 60):match.start()]
        after = text[match.end():match.end() + 12]
        named_skill = (re.search(r'\b(?:use|using|apply|open|follow|load|invoke|run)\s+(?:the\s+)?$', before, re.I)
                       and re.match(r'\s+skill\b', after, re.I)) or re.search(
                           r'\b(?:use|using|apply|open|follow|load|invoke|run)\s+(?:the\s+)?(?:installed\s+)?skill\s+$', before, re.I)
        # An explicit invocation inside a quotation is task data; a quoted name
        # inside an actual skill invocation is an argument to that invocation.
        if '$' not in body and (filename or (named_skill and re.fullmatch(r'[\w-]+', body))):
            return match[0]
        return ' '

    return quoted.sub(keep_argument, text)


def _positive_actions(prompt: str) -> str:
    """Do not turn a negated action into an implicit workflow request."""
    return re.sub(
        r"\b(?:do not|don't|never|avoid|skip|without)\b.*?(?=[.;,!?:\n]|\b(?:but|instead|then|and)\b|$)",
        ' ', prompt, flags=re.I)


def _explicit_position(prompt: str, name: str) -> int | None:
    quoted = r'[\x22\x27`]?'
    skill_name = re.escape(name)
    pattern = re.compile(
        rf'(?<![\w$])\${skill_name}(?![\w-])|'
        rf'\b(?:use|using|apply|open|follow|load|invoke|run)\s+(?:the\s+)?{quoted}{skill_name}{quoted}\s+skill\b|'
        rf'\b(?:use|using|apply|open|follow|load|invoke|run)\s+(?:the\s+)?(?:installed\s+)?skill\s+{quoted}{skill_name}(?![\w-])', re.I)
    negated = False
    for match in pattern.finditer(prompt):
        before = prompt[max(0, match.start() - 40):match.start()]
        if not re.search(r"(?:do not|don't|never|avoid|without)(?:\s+(?:use|using|load|loading|apply))?\s*$", before, re.I):
            return match.start()
        negated = True
    return -1 if negated else None


def _read_workflow(skill: loader.FileSkill, limit: int) -> str:
    """Read only the entrypoint, without expanding bundled references or scripts."""
    with skill.manifest.open(encoding='utf-8', errors='replace') as stream:
        text = stream.read(loader.HEADER_MAX_CHARS + limit + 1)
    if text.startswith('---'):
        end = re.search(r'\n---[ \t]*(?:\n|$)', text[3:])
        if end:
            text = text[3 + end.end():]
    text = text.strip()
    if len(text) <= limit:
        return text
    suffix = (f'\n[Workflow shortened. Before following this workflow, call '
              f'skill_open(name="{skill.name}") for all required steps.]')
    return text[:max(0, limit - len(suffix))].rstrip() + suffix if limit >= len(suffix) else ''


def select_for_task(prompt: str, skills: Iterable[loader.FileSkill] | None = None,
                    *, max_chars: int = MAX_GUIDANCE_CHARS) -> TaskGuidance:
    """Choose at most two relevant workflows; explicit names outrank task matches.

    Only shipped curated packages match implicitly. Opted-in external packages
    can be selected by exact $name or 'Use the NAME skill', including the desktop
    picker syntax. The caller supplies the raw current request, not tool output.
    """
    budget = max(0, int(max_chars))          # a caller with a large window may pass more than the default (guidance_budget)
    prompt = _request_text(prompt)
    opt_out = re.search(r"\b(?:do not|don't|never|avoid|skip|without)\s+(?:(?:use|using|load|loading|apply|applying)\s+)?(?:(?:any|all|automatic|extra|additional|installed)\s+)?skills?\b", prompt, re.I)
    if budget < 160 or not prompt.strip() or opt_out:
        return TaskGuidance()
    if skills is None:
        from ..tools.installed_skill_tools import installed
        skills = installed()
    # Bound work on pasted files while preserving instructions at either end.
    prompt = prompt if len(prompt) <= 32000 else prompt[:24000] + '\n' + prompt[-8000:]
    positive = _positive_actions(prompt)
    candidates = []
    for skill in skills:
        if not loader.enabled(skill):
            continue
        explicit = _explicit_position(prompt, skill.name)
        excluded = re.search(rf"\b(?:without|avoid|skip)\s+(?:the\s+)?\$?{re.escape(skill.name)}\s+skill\b", prompt, re.I)
        if explicit == -1 or (explicit is None and excluded):
            continue
        matches = [bool(pattern.search(positive)) for pattern in _PATTERNS.get(skill.name, ())] if skill.curated else []
        acted = bool(matches) and any(matches[index] for index in _ACTION_PATTERN_INDEX.get(skill.name, ()))
        score = sum(matches) + 4 * acted if matches else 0
        if explicit is not None or score:
            candidates.append((explicit is None, explicit if explicit is not None else -score,
                               skill.name.casefold(), skill, acted))
    present = {row[3].name for row in candidates}
    candidates = [row for row in candidates
                  if not (row[0] and any(row[3].name in _DISPLACES.get(name, ())
                                         and not (row[4] and name in _DISPLACES_ONLY_INCIDENTAL) for name in present))]
    candidates.sort(key=lambda row: row[:3])
    if not candidates:
        return TaskGuidance()
    prefix = 'Task workflows: use the relevant steps below with the current request and available tools.\n'
    parts, names, tools, warnings = [], [], [], []
    remaining = budget - len(prefix)
    for _, _, _, skill, _ in candidates:
        if len(names) == MAX_WORKFLOWS:
            break
        if skill.name in names:
            continue
        title = f'\n### {skill.name}\n'
        # the first (best-ranked) skill takes the whole budget when the caller allowed more than the short default; a
        # second skill, and every skill under the default budget, keeps the 2,200-char summary
        room = remaining - len(title) if not names and budget > MAX_GUIDANCE_CHARS else min(2200, remaining - len(title))
        if room < 160:
            break
        try:
            body = _read_workflow(skill, room)
        except OSError as exc:
            warnings.append(f"Skill {skill.name} could not be read: {exc}")
            continue
        if not body:
            continue
        parts.append(title + body)
        remaining -= len(title) + len(body)
        names.append(skill.name)
        if '[Workflow shortened.' in body:
            warnings.append(f'Skill {skill.name} guidance shortened; open the full workflow before following it.')
            if 'skill_open' not in tools:
                tools.insert(0, 'skill_open')
        # Manifest capabilities are hints, never execution permissions. Only the
        # curated manifests contain the reviewed Dream tool vocabulary.
        if skill.curated:
            tools.extend(name for name in skill.capabilities
                         if re.fullmatch(r'[a-z][a-z0-9_]{0,79}', name) and name not in tools)
    return TaskGuidance(prefix + ''.join(parts) if parts else '', tuple(names), tuple(tools[:8]), tuple(warnings))
