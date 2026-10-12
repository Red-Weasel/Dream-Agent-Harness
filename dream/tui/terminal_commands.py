"""Local owner-prompt discovery; completion never executes a command or model."""
from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from ..core.effort import LEVELS
from ..core.model_catalog import model_choices


# Only commands actually handled by App._command belong here. Native provider
# terminals supply their own command menus instead of using this completer.
COMMANDS = {
    'help': 'Show terminal commands and shortcuts',
    'model': 'Choose or change this provider’s model',
    'effort': 'Show or change reasoning effort',
    'skill': 'Invoke an enabled skill: name followed by your task',
    'plugin': 'Invoke an enabled plugin: name followed by your task',
    'login': 'Open a provider sign-in window',
    'status': 'Show effective context, profile and execution state',
    'settings': 'Inspect or change settings and their sources',
    'council': 'Ask the configured frontier advisors',
    'agents': 'List available sub-agents',
    'plugins': 'Show loaded plugins and loading failures',
    'mcp': 'List connected MCP servers and their tools',
    'profile': 'Choose the context profile for the next session',
    'preset': 'Choose the skills and tools preset for the next session',
    'extensions': 'List, enable or disable extensions',
    'learn': 'Record a demonstration and draft a skill',
    'red-team': 'Start or stop a scoped local exercise',
    'loop': 'Work autonomously toward your goal',
    'runs': 'List saved autonomous runs',
    'loop-resume': 'Resume a saved autonomous run',
    'recall': 'Search long-term memory',
    'remember': 'Save a memory: title :: body',
    'memory': 'List semantic or procedural memories',
    'sessions': 'List recent conversations',
    'tasks': 'List, add or finish open work',
    'toolcalls': 'Set the tool-call budget per prompt',
    'maxtokens': 'Set this session’s output-token ceiling',
    'export': 'Export the conversation or file it in the Library',
    'library': 'Search and manage your Library',
    'verbose': 'Toggle full live tool output',
    'errors': 'Show this session’s errors',
    'changes': 'List confirmed file changes and tool calls in this session',
    'instructions': 'Read or edit your standing preferences',
    'thoughts': 'Choose persistent or ephemeral reasoning',
    'optimizer': 'Prompt optimizer on, off, or skip the next message',
    'mind': 'Show the memory map',
    'monitor': 'Toggle GPU and inference telemetry',
    'plan': 'Show the current task plan',
    'critique': 'Ask a provider to critique the latest render',
    'rewind': 'List or restore file checkpoints',
    'review': 'Review the working diff or a base ref',
    'handoff': 'Create a conversation handoff between turns',
    'new': 'Consolidate this session and start fresh',
    'clear': 'Clear the screen',
    'quit': 'Consolidate and exit Dream',
}


def _display(value: str, limit: int = 180) -> str:
    """Descriptions are literal text, never terminal controls or rich markup."""
    return ' '.join(''.join(c for c in value if c.isprintable() or c.isspace()).split())[:limit]


def model_options(provider: str, current: str | None = None) -> list[tuple[str, str]]:
    """Offline suggestions; presence here does not establish account access."""
    values = {item['id']: item['label'] for item in model_choices(provider)}
    if current and current not in values:
        values = {current: current, **values}
    return [(name, _display(label) + (' · current' if name == current else ''))
            for name, label in values.items()]


def _extension_options(kind: str) -> Iterable[tuple[str, str]]:
    # Discovery reads metadata only. Full bodies are opened by expand_invocation
    # after the owner submits the chosen skill and task, never while typing.
    if kind == 'skill':
        from ..tools.installed_skill_tools import installed
        return [(skill.name, skill.description) for skill in installed()]
    from .. import plugins
    return [(plugin.name, plugin.description) for plugin in plugins.loaded() if plugin.enabled]


def _name_prefix(tail: str) -> str | None:
    """Only complete the invocation name; leave the following task untouched."""
    if tail.startswith(('"', "'")):
        end = tail.find(tail[0], 1)
        if end >= 0:
            return None if tail[end + 1:] else tail[1:end]
        return tail[1:]
    return None if any(c.isspace() for c in tail) else tail


class TerminalCompleter(Completer):
    """Pass callables so model/provider switches are reflected immediately."""

    def __init__(self, provider: Callable[[], str], current_model: Callable[[], str | None] = lambda: None):
        self.provider = provider
        self.current_model = current_model

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/') or '\n' in document.text:
            return
        command, separator, tail = text[1:].partition(' ')
        if not separator:
            for name, description in COMMANDS.items():
                if name.startswith(command.lower()):
                    yield Completion('/' + name, start_position=-len(text), display_meta=description)
            return
        command = command.lower()
        if command == 'model':
            options = model_options(self.provider(), self.current_model())
        elif command in {'skill', 'plugin'}:
            prefix = _name_prefix(tail)
            if prefix is None:
                return
            try:
                options = list(_extension_options(command))
            except (OSError, ValueError):
                return
            for name, description in options:
                if name.lower().startswith(prefix.lower()) and name == _display(name, len(name)):
                    yield Completion(shlex.quote(name) + ' ', start_position=-len(tail),
                                     display=name, display_meta=_display(description))
            return
        elif command == 'login':
            options = [(key, label) for key, label in (
                ('claude', 'Claude'), ('codex', 'Codex'), ('grok', 'Grok'), ('agy', 'Gemini · Antigravity'))]
        elif command == 'effort':
            options = [(level, 'Reasoning effort') for level in LEVELS]
        else:
            return
        for name, label in options:
            if name.lower().startswith(tail.lower()) or label.lower().startswith(tail.lower()):
                yield Completion(name, start_position=-len(tail), display_meta=label)
