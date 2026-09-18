"""Dream. Your models. Your workspace.

This is home. The agent wakes up here remembering who it is, reaches for tools that
were shaped around how it works, gets better over time by writing playbooks for
itself, and — when it hits a wall — builds the tool it needs and keeps it.
"""

__version__ = "0.1.0"

# Capture source identity at package startup, before a later status request.
from . import runtime_identity as _runtime_identity
