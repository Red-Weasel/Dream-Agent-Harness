"""DREAM-085: Dream's own refusal of a tool call, as distinct from the owner's No.

The permission callback returns True (run) or False (the owner declined). When Dream itself stops a call -- a policy
block, shell authorization that changed while the prompt was open, a file the loop manages -- the callback raises
PermissionRefused instead, so the model is told what actually happened rather than that the owner said no.
"""


class PermissionRefused(Exception):
    """Dream, not the owner, stopped this tool call; the message says why."""


def refusal_text(exc: BaseException) -> str:
    """The tool result for a permission check that did not grant: Dream's reason, or the check's own failure."""
    if isinstance(exc, PermissionRefused):
        return f"Not run: {exc}"
    return f"Not run: the permission check failed ({type(exc).__name__}: {exc})"
