"""Explicit owner skill/plugin invocation; metadata discovery never opens bodies."""
from __future__ import annotations

import shlex

from . import turn_origin


class InvokedPrompt(str):
    """A server-created string carrying the exact owner-task span through input queues."""

    def __new__(cls, text: str, origin: str):
        prompt = super().__new__(cls, text)
        prompt.origin = origin
        return prompt


def _wrapped(prefix: str, task: str) -> InvokedPrompt:
    return InvokedPrompt(prefix + task, f'{turn_origin.INVOKED}:{len(prefix)}:{len(task)}')


def expand_invocation(text: str) -> str:
    if not text.startswith(('/skill ', '/plugin ')):
        return text
    command, rest = text.split(' ', 1)
    lexer = shlex.shlex(rest, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        name = next(lexer)
    except StopIteration:
        raise ValueError('Choose a skill or plugin and add your task.')
    # shlex's token positions include the separating whitespace; preserve task
    # quotes/newlines instead of round-tripping the whole prompt as shell tokens.
    task = rest[lexer.instream.tell():].strip()
    if not task:
        raise ValueError('Add your task after the skill or plugin name.')
    from ..tools.installed_skill_tools import installed
    from ..skills import loader
    if command == '/skill':
        matches = [s for s in installed() if s.name == name]
        if len(matches) != 1:
            raise ValueError('That skill is not enabled in this session. Choose it from the slash menu.')
        skill = matches[0]
        body = loader.load_body(skill)
        if isinstance(body, loader.SkillReadFailure):
            raise ValueError('The selected skill could not be opened.')
        return _wrapped(
            f'The owner explicitly invoked the enabled skill {name}. Use this procedure within the existing permissions.\n'
            f'Skill directory: {skill.root}\n\n{body}\n\nOwner task:\n', task)
    from .. import plugins
    matches = [p for p in plugins.loaded() if p.name == name and p.enabled]
    if len(matches) != 1:
        raise ValueError('That plugin is not enabled. Review it in Settings before use.')
    plugin = matches[0]
    return _wrapped(f'The owner explicitly invoked the enabled plugin {plugin.name}: {plugin.description}.\n'
            f'Use its available capabilities ({", ".join(plugin.parts) or "none declared"}). '
            'Discover and open the relevant skill or tool when needed; existing permissions still apply.\n\n'
            'Owner task:\n', task)
