"""Exercise the standalone audit with disposable ZIP wheels, without Dream imports."""
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_distribution.py"
ASSETS = {
    "dream/gui/static/controls.js", "dream/gui/static/companion.js",
    "dream/gui/static/media.js", "dream/gui/static/media.css",
    "dream/media/player.html", "dream/media/cli.py", "dream/desktop/window.py",
    "dream/resources/skills/illuminati-handshake/SKILL.md",
    "dream/gui/static/prompt_optimizer.js", "dream/gui/static/prompt_optimizer.css",
    "dream/gui/static/projects.js", "dream/gui/static/projects.css",
    "dream/gui/static/turn-timing.js", "dream/gui/static/turn-timing.css",
}


class DistributionAuditTests(unittest.TestCase):
    def audit(self, extras=(), missing=()):
        with tempfile.TemporaryDirectory(prefix="dream-wheel-audit-") as directory:
            wheel = Path(directory) / "dream-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name in sorted(ASSETS - set(missing)):
                    archive.writestr(name, "fixture")
                archive.writestr("dream-0.1.0.dist-info/METADATA", "Name: dream\nVersion: 0.1.0\n")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    for name in extras:
                        archive.writestr(name, "synthetic canary")
            result = subprocess.run([sys.executable, str(SCRIPT), directory],
                                    text=True, capture_output=True, check=False)
            return result, wheel.read_bytes()

    def test_valid_package_and_json_report(self):
        result, data = self.audit(["dream/memory/store.py", "dream/resources/skills/coding/SKILL.md"])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report, {
            "wheel": "dream-0.1.0-py3-none-any.whl", "files": len(ASSETS) + 3,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "layout_audit": "passed",
        })

    def test_rejects_nested_private_directories(self):
        for part in (".codex", ".agents", ".codebase-memory", ".claude", ".remember",
                     ".dream", ".git", "data", "var", "artifacts", "__pycache__"):
            with self.subTest(part=part):
                result, _ = self.audit([f"dream/resources/{part}/canary.json"])
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rejects_foreign_metadata_namespaces(self):
        for name in ("foreign.dist-info/canary.json", "outside/foreign.dist-info/canary.json",
                     "dream-9.9.dist-info/METADATA", "dream/vendor.dist-info/canary.json"):
            with self.subTest(name=name):
                result, _ = self.audit([name])
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rejects_duplicate_members(self):
        result, _ = self.audit(["dream/gui/static/controls.js"])
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rejects_noncanonical_paths(self):
        for name in ("/dream/canary.json", "dream/../canary.json", "dream//canary.json",
                     "dream/./canary.json", "dream/evil\\canary.json", "dream/C:/canary.json",
                     "dream/control\ncanary.json"):
            with self.subTest(name=name):
                result, _ = self.audit([name])
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rejects_file_ancestor_collisions_in_either_order(self):
        for extras in [
            ["dream/gui"],
            ["dream-0.1.0.dist-info/METADATA/item"],
            ["dream/collision", "dream/collision/child"],
            ["dream/collision/child", "dream/collision"],
        ]:
            with self.subTest(extras=extras):
                result, _ = self.audit(extras)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rejects_symlink_entries(self):
        link = zipfile.ZipInfo("dream/link.json")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        result, _ = self.audit([link])
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_requires_current_ui_assets(self):
        for name in sorted(ASSETS):
            with self.subTest(name=name):
                result, _ = self.audit(missing=[name])
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("missing runtime assets", result.stderr)

    def test_rejects_runtime_file_types(self):
        for name in ("state.db", "state.sqlite", "state.sqlite3", "model.gguf",
                     "model.safetensors", "model.onnx", "debug.log", "code.pyc", ".env.local"):
            with self.subTest(name=name):
                result, _ = self.audit([f"dream/{name}"])
                self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
