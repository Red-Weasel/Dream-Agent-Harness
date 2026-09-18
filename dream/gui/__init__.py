"""Dream Studio — the GUI half of the harness.

The terminal is still the harness. This package adds a second, graphical view of
the SAME session: it subscribes to the engine's event stream, it does not own it.
Nothing in here may block, slow, or crash the TUI — see ``bus.EventBus``.
"""
