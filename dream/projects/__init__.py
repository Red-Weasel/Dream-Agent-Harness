"""Explicit, CPU-only project context."""
from .workspace import ProjectError, ProjectWorkspace, StaleRevision, build_context

__all__ = ['ProjectError', 'ProjectWorkspace', 'StaleRevision', 'build_context']
