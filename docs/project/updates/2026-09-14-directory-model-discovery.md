# DREAM-077 — DeepSeek V4.1 directory discovery

Recorded: `2026-09-14T22:57:00.559824+00:00`
Work items: `DREAM-077`
Outcome: `implemented`
Actor: Codex.

## Request

Owner reports only five models and missing DeepSeek V4.1 after the width repair.
Acceptance: locate the missing model, include its supported directory format,
read settings correctly and preserve model identity/size checks. No model loads.
Baseline `/tmp/dream-safetensors-20260914.json`: 788 source hashes. Existing dirty
work preserved. This corrects the incomplete scope of the previous five-model
observation; it does not rewrite that historical handoff.

## Changes

Found the installed model at `~/models/DeepSeek-V4.1-Flash`, stored as48 indexed
Safetensors shards. Dream only discovered GGUFs. The installed engine's metadata
command reports this directory as supported `deepseek_v41` with a streaming
memory planner. Its source/docs confirm a directory branch in the existing server.
No engine edits were needed. Engine graph text query returned no hits, so focused
source and prose reads were used after Dream graph discovery located the callers.

The bounded scanner now finds candidate checkpoint folders alongside GGUFs.
Directory validation allows DeepSeek V4.1, requires a bounded JSON index and all
nonempty indexed shards, rejects nonlocal shard names and deduplicates folders.
Size sums each shard once. Metadata reads config context instead of opening the
folder as a GGUF. Preset identity stats each shard plus metadata and prepared
banks so in-place changes invalidate saved choices. CLI preflight uses total
Safetensors size instead of the directory inode size. GPU/loading guards unchanged.

Other observed missing model links point to unavailable `/media/<user>/Data1`.
The model storage was not mounted or altered. Other Safetensors architectures
remain outside this engine-specific directory support.

## Validation

Existing `.venv`, model-free fixtures, this date:

- Initial regression run:4 failed,5 passed. Failures reproduced missing discovery,
  missing desktop catalog entry, directory metadata error and unchanged identity
  after an in-place shard write.
- Initial combined sandbox test run stalled after partial output; interrupted with
  exit130. Cause not established. Rerun with local socket access passed56 tests.
  Separate startup/preflight/tuning/residency group passed108 tests.
- Extended directory tests passed12, including prepared-bank identity, metadata
  read budget and actual CLI size delivery to a fixture that refuses loading.
- Final combined command: `.venv/bin/pytest -q tests/test_directory_models.py
  tests/test_models.py tests/test_model_scan_bounds.py tests/test_model_presets.py
  tests/test_desktop_startup.py tests/test_local_preflight.py tests/test_local_tuning.py
  tests/test_model_residency.py`: 167 passed in 5.76 seconds, no skips.
- `python3 scripts/check_project_tracking.py check --snapshot
  /tmp/dream-safetensors-20260914.json` passed: 89 dated records, nine changed
  source paths. `git diff --check` passed.
- Actual `startup.catalog()` now returns six local choices, including
  `DeepSeek-V4.1-Flash · Safetensors · 510.3 GB · home`. All five previous GGUF
  choices remain. The size is on-disk indexed weights, not measured RAM usage.
- Actual installed engine capability inspection returned supported/streaming.
  Actual `startup.model_settings()` with hardware inspection replaced by an empty
  fixture returned architecture `deepseek_v41`, context limit1048576, context32768
  and high effort, without metadata warnings. No recommendation GPU probe or
  tensor load performed. Owner saved presets were only read.

## Unfinished work

No remaining discovery/settings implementation within this scope. Real loading,
resource fit and inference remain untested. Existing preflight conservatively
compares full indexed weight size with RAM for streaming; this pass does not
qualify whether V4.1's engine-managed residency can fit a particular machine.
No model loads, GPU workloads, drive mounts, owner restart or acceptance.
No task-owned tests or workers remain running.

## Next steps

Owner clicks Refresh to request the updated catalog subprocess. The widened
native panel also takes effect at the next normal application restart. Further
missing models require their storage paths/formats; unavailable Data1 links need
restored drive access before discovery. Live load qualification is separate and
requires the standing GPU preflight. Preserve dirty work and take a fresh snapshot.

## Files changed

- `dream/local/models.py`
- `dream/local/model_defaults.py`
- `dream/local/model_presets.py`
- `dream/local/launcher.py`
- `tests/test_directory_models.py`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-directory-model-discovery.md`
