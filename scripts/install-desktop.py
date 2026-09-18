#!/usr/bin/env python3
"""Install a per-user applications-menu entry for this Dream checkout."""
from pathlib import Path
import os

root = Path(__file__).resolve().parents[1]
launcher = root / 'scripts/dream-desktop'
launcher.chmod(launcher.stat().st_mode | 0o111)
applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
applications.mkdir(parents=True, exist_ok=True)
# Desktop Exec uses its own quoting rules, not shell quoting.
quoted = str(launcher).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')
entry = applications / 'dream-desktop.desktop'
content = ('[Desktop Entry]\nType=Application\nName=Dream\nGenericName=AI Workspace\n'
           'Comment=Your Dream terminal, Studio and browser in one workspace\n'
           f'Exec="{quoted}"\nIcon={root / "dream/gui/static/dream-app-icon.png"}\n'
           'Terminal=false\nCategories=Development;Utility;\nStartupWMClass=Dream\n')
entry.write_text(content)
entry.chmod(0o644)
print(f'Dream is available in your applications menu: {entry}')
