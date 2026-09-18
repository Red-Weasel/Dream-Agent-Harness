"""Pluggable model backends. Each turns a prompt into a stream of ``Event`` objects and
executes tool calls; the Engine stays backend-agnostic and just logs the events."""
