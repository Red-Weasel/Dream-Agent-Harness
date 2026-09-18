"""Exercise learning artifacts without capturing the live desktop or loading an LLM."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dream import capability_lab, demonstrations, config


def test_rl_scaffold_trains_policy_and_preserves_domain_uncertainty(tmp_path):
    lab = capability_lab.create(tmp_path, "inspect-then-act", "Learn a reliable action order",
                                ["Check state before acting", "Verify held-out outcomes"], method="rl")
    result = subprocess.run([sys.executable, "train.py", "--episodes", "100"],
                            cwd=lab["directory"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["candidate"]["success_rate"] == 1
    assert report["candidate"]["success_rate"] > report["baseline"]["success_rate"]
    assert report["train_seed_range"][1] < report["evaluation_seed_range"][0]
    assert report["domain_acceptance_verified"] is False
    inspected = capability_lab.inspect(tmp_path, lab["id"])
    assert any(f["file"] == "environment.py" and f["sha256"] for f in inspected["files"])


def test_lab_refuses_workspace_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".dream").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        capability_lab.create(workspace, "test", "goal", ["criterion"])


def prepared_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    folder = demonstrations.directory("demo-fixture")
    (folder / "frames").mkdir(parents=True)
    (folder / "frames/00001.jpg").write_bytes(b"fixture image bytes")
    info = {"id": "demo-fixture", "name": "Example", "status": "ready",
            "frames": [{"file": "frames/00001.jpg", "seconds": 0}]}
    (folder / "manifest.json").write_text(json.dumps(info))
    return folder


def test_draft_requires_evidence_and_installs_disabled(tmp_path, monkeypatch):
    from dream import extensions
    folder = prepared_demo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="cite frames"):
        demonstrations.create_draft("demo-fixture", goal="Open a project", steps=[{"action": "click", "frames": ["../secret"]}])
    path = demonstrations.create_draft("demo-fixture", goal="Open a project", steps=[{
        "action": "Open the project picker", "frames": ["frames/00001.jpg"], "basis": "inferred",
        "verify": "The project list is visible"}], uncertainty="The click itself was not logged")
    assert path.exists() and "inferred" in path.read_text()
    installed = demonstrations.install("demo-fixture")
    assert (installed / "frames/00001.jpg").read_bytes() == b"fixture image bytes"
    assert not extensions.is_enabled("skill:" + installed.name)


def test_recorded_skill_installs_privately_and_is_discovered_after_enable(tmp_path, monkeypatch):
    from dream import extensions, plugins
    from dream.tools import installed_skill_tools
    from dream.skills import loader

    folder = prepared_demo(tmp_path, monkeypatch)
    monkeypatch.delenv("DREAM_SKILL_DIRS", raising=False)
    monkeypatch.delenv("DREAM_EXTERNAL_SKILLS", raising=False)
    monkeypatch.setattr(plugins, "_LOADED", [])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    draft = demonstrations.create_draft("demo-fixture", goal="Open a project", steps=[{
        "action": "Open the project picker", "frames": ["frames/00001.jpg"], "basis": "visible"}])
    original = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    installed = demonstrations.install("demo-fixture")

    assert installed == config.DATA_DIR / "skills" / "learned-demo-fixture"
    assert not (config.ROOT / "skills" / installed.name).exists()
    assert (installed / "SKILL.md").read_bytes() == draft.read_bytes()
    assert (installed / "frames/00001.jpg").read_bytes() == b"fixture image bytes"
    assert (installed / "evidence.json").read_bytes() == (folder / "draft/evidence.json").read_bytes()
    assert original == {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert installed.name in {s.name for s in installed_skill_tools.inventory(refresh=True)}
    assert installed.name not in {s.name for s in installed_skill_tools.installed()}

    extensions.set_enabled("skill:" + installed.name, True)
    skill = next(s for s in installed_skill_tools.installed(refresh=True) if s.name == installed.name)
    assert loader.load_body(skill).startswith(draft.read_text())
    assert "frames/00001.jpg" in loader.bundled_names(skill)


@pytest.mark.parametrize("reference", ["../../outside.jpg", "manifest.json"])
def test_invalid_recorded_evidence_leaves_no_installed_package(tmp_path, monkeypatch, reference):
    prepared_demo(tmp_path, monkeypatch)
    demonstrations.create_draft("demo-fixture", goal="Open a project", steps=[{
        "action": "Open the project picker", "frames": ["frames/00001.jpg"]}])
    folder = demonstrations.directory("demo-fixture")
    evidence = folder / "draft/evidence.json"
    payload = json.loads(evidence.read_text())
    payload["steps"][0]["frames"] = [reference]
    evidence.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Invalid frame reference"):
        demonstrations.install("demo-fixture")
    assert not (config.DATA_DIR / "skills/learned-demo-fixture").exists()
    assert not (config.ROOT / "skills/learned-demo-fixture").exists()
    assert evidence.read_text() == json.dumps(payload)


@pytest.mark.asyncio
async def test_recorder_never_starts_without_valid_explicit_region(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    recorder = demonstrations.Recorder()
    with pytest.raises(ValueError, match="region"):
        await recorder.start("test", region=(0, 0, 0, 0))
    assert recorder.process is None


@pytest.mark.asyncio
async def test_extracts_an_imported_synthetic_video(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    source = tmp_path / "synthetic.mkv"
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             "color=c=blue:s=160x120:r=2", "-t", "1", "-c:v", "libx264", str(source)],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    info = demonstrations.import_video(source, "Synthetic fixture")
    ready = await demonstrations.extract(info["id"])
    assert ready["status"] == "ready" and len(ready["frames"]) == 2
    assert (demonstrations.directory(info["id"]) / ready["frames"][0]["file"]).is_file()


async def test_cancelled_stop_reaps_recorder_without_capturing_screen(monkeypatch):
    import asyncio
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(demonstrations.shutil, "which", lambda name: sys.executable)
    original_spawn = asyncio.create_subprocess_exec
    async def fixture_spawn(*args, **kwargs):
        return await original_spawn(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fixture_spawn)
    recorder = demonstrations.Recorder()
    await recorder.start("Synthetic recorder process", region=(0, 0, 100, 100))
    process = recorder.process
    stopping = asyncio.create_task(recorder.stop())
    await asyncio.sleep(.02)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert process.returncode is not None and recorder.process is None
    await asyncio.wait_for(recorder._watcher, 1)
    assert recorder.info["status"] == "failed"
