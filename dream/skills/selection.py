"""Small deterministic task workflows. No inference or external catalog expansion."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from . import loader

MAX_GUIDANCE_CHARS = 4000
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
        r'\b(?:implement|fix|build|change|add|write|create)\b.{0,65}\b(?:code|script|test|endpoint|page|site|app|feature|button|form)\b',
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
    'verifying': (
        r'\b(?:verify|verification|validate|sanity.check|acceptance check|regression|recover|recovery|stuck|blocked|retry|interrupted|resume task)\b',
        r'\b(?:command|tool|export|operation)\b.{0,35}\b(?:failed|failure|error|timed out|timeout)\b',
    ),
}
_PATTERNS = {name: tuple(re.compile(pattern, re.I) for pattern in patterns)
             for name, patterns in _RULES.items()}
# Action-bearing patterns outrank incidental format/language nouns when the
# two-workflow admission budget is contested. Explicit skill requests still win.
_ACTION_PATTERN_INDEX = {
    'computer-use': (0, 1, 2, 3, 4), 'coding': (2,), 'research': (0, 2),
    'writing': (0,), 'documents': (1,), 'data-analysis': (1,), 'media': (1,),
    'library': (1,), 'verifying': (1,),
}


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
        named_skill = (re.search(r'\b(?:use|using|apply|open|follow|load|invoke)\s+(?:the\s+)?$', before, re.I)
                       and re.match(r'\s+skill\b', after, re.I)) or re.search(
                           r'\b(?:use|using|apply|open|follow|load|invoke)\s+(?:the\s+)?(?:installed\s+)?skill\s+$', before, re.I)
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
        rf'\b(?:use|using|apply|open|follow|load|invoke)\s+(?:the\s+)?{quoted}{skill_name}{quoted}\s+skill\b|'
        rf'\b(?:use|using|apply|open|follow|load|invoke)\s+(?:the\s+)?(?:installed\s+)?skill\s+{quoted}{skill_name}(?![\w-])', re.I)
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
    budget = max(0, min(int(max_chars), MAX_GUIDANCE_CHARS))
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
        score = sum(matches) + 4 * any(matches[index] for index in _ACTION_PATTERN_INDEX.get(skill.name, ())) if matches else 0
        if explicit is not None or score:
            candidates.append((explicit is None, explicit if explicit is not None else -score,
                               skill.name.casefold(), skill))
    candidates.sort(key=lambda row: row[:3])
    if not candidates:
        return TaskGuidance()
    prefix = 'Task workflows: use the relevant steps below with the current request and available tools.\n'
    parts, names, tools, warnings = [], [], [], []
    remaining = budget - len(prefix)
    for _, _, _, skill in candidates:
        if len(names) == MAX_WORKFLOWS:
            break
        if skill.name in names:
            continue
        title = f'\n### {skill.name}\n'
        room = min(2200, remaining - len(title))
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
