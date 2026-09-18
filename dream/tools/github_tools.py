"""GitHub, for design context: explore a repository and import the files that
carry its visual vocabulary — theme tokens, the components the user named, global
stylesheets — so a mock is built from what the app actually is, not from a
recollection of it.

Runs on the `gh` CLI (`gh api`), so the user's own login and permissions apply and
Dream never holds a token. `gh` missing or logged out is a plain error naming the
fix. The tree is a menu, not the meal: the chain is get_tree → import_files →
read_file on what landed.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .context import ctx, err, ok

_MAX_IMPORT_FILES = 60
_MAX_FILE_BYTES = 2 * 1024 * 1024
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+$")


async def _gh(*args: str, timeout: float = 60.0) -> tuple[int, str]:
    """Run `gh api …`; (returncode, stdout-or-stderr text)."""
    exe = shutil.which("gh")
    if not exe:
        return 127, "the GitHub CLI (`gh`) is not installed — `sudo apt install gh` then `gh auth login`"
    from ..core.execution import _trusted_system_file, minimal_environment, run_owned
    if not _trusted_system_file(Path(exe)):
        return 127, "The GitHub tool requires a trusted system installation of gh; a writable workspace executable cannot use host credentials."
    try:
        env = minimal_environment({"PATH": "/usr/local/bin:/usr/bin:/bin", "GH_PROMPT_DISABLED": "1", "GH_PAGER": ""},
                                  inherit=("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR"))
        result = await run_owned([str(Path(exe).resolve()), *args], cwd=Path.home(), env=env,
                                 timeout=timeout, max_output=4 * 1024 * 1024)
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"
    if result.timed_out:
        return 124, f"`gh {' '.join(args[:3])}…` timed out after {timeout:g}s; its owned processes were stopped"
    if result.truncated:
        return 1, "GitHub response exceeded 4 MiB; narrow the repository/path before retrying"
    text = result.output.decode("utf-8", "replace")
    if result.returncode != 0 and "auth login" in text.lower():
        text = "not logged in to GitHub — run `gh auth login` in a terminal, then retry"
    return result.returncode or 0, text


def _repo(owner: str, repo: str) -> str | None:
    if not (_SLUG.match(owner or "") and _SLUG.match(repo or "")):
        return None
    return f"{owner}/{repo}"


@tool(
    "github_list_repos",
    "List repositories the user can see on GitHub (name, default branch, description), "
    "newest first, so a bare github.com/OWNER/REPO URL can be resolved to its default "
    "branch. Pass `owner` to list one user's or organisation's repos, or a `query` to "
    "search by name.",
    {"type": "object", "properties": {"owner": {"type": "string"}, "query": {"type": "string"},
                                      "limit": {"type": "number"}}, "required": []},
)
async def github_list_repos(args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 30), 100))
    owner, query = str(args.get("owner") or "").strip(), str(args.get("query") or "").strip()
    if owner and not _SLUG.match(owner):
        return err("owner must be a GitHub login.")
    if query:
        rc, out = await _gh("api", "-X", "GET", "search/repositories", "-f", f"q={query}",
                            "-f", f"per_page={limit}", "--jq",
                            ".items[] | {name: .full_name, default_branch, description, updated: .updated_at}")
    elif owner:
        rc, out = await _gh("api", f"users/{owner}/repos?per_page={limit}&sort=updated", "--jq",
                            ".[] | {name: .full_name, default_branch, description, updated: .updated_at}")
    else:
        rc, out = await _gh("api", f"user/repos?per_page={limit}&sort=updated", "--jq",
                            ".[] | {name: .full_name, default_branch, description, updated: .updated_at}")
    if rc != 0:
        return err(out.strip() or f"gh exited {rc}")
    rows = [json.loads(l) for l in out.splitlines() if l.strip()]
    if not rows:
        return ok("No repositories found.")
    return ok("\n".join(f"{r['name']}  (default: {r.get('default_branch')})  {r.get('description') or ''}".rstrip()
                        for r in rows))


@tool(
    "github_get_tree",
    "List the files of a GitHub repository at a ref (branch, tag, or commit), optionally "
    "under a path prefix. Names only — the tree is a menu: follow with "
    "github_import_files for the files that matter (theme/color tokens, the components "
    "the user named, global stylesheets, layout scaffolds), then read_file on what landed.",
    {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"},
                                      "ref": {"type": "string", "description": "Branch, tag, or SHA (default: the default branch)."},
                                      "path_prefix": {"type": "string"}, "limit": {"type": "number"}},
     "required": ["owner", "repo"]},
)
async def github_get_tree(args: dict[str, Any]) -> dict[str, Any]:
    full = _repo(str(args.get("owner") or ""), str(args.get("repo") or ""))
    if not full:
        return err("owner and repo must be plain GitHub names.")
    ref = str(args.get("ref") or "").strip() or "HEAD"
    prefix = str(args.get("path_prefix") or "").strip().strip("/")
    limit = max(1, min(int(args.get("limit") or 500), 5000))
    rc, out = await _gh("api", f"repos/{full}/git/trees/{ref}?recursive=1", "--jq",
                        ".truncated as $t | .tree[] | select(.type == \"blob\") | \"\\(.path)\\t\\(.size // 0)\"")
    if rc != 0:
        return err(out.strip() or f"gh exited {rc}")
    rows = [l.split("\t") for l in out.splitlines() if l.strip()]
    if prefix:
        rows = [r for r in rows if r[0] == prefix or r[0].startswith(prefix + "/")]
    total = len(rows)
    rows = rows[:limit]
    if not rows:
        return ok(f"No files under '{prefix or '/'}' at {full}@{ref}.")
    body = "\n".join(f"{p}  ({int(s or 0):,} B)" for p, s in rows)
    more = f"\n… {total - len(rows)} more; narrow with path_prefix" if total > len(rows) else ""
    return ok(f"{full}@{ref}: {total} file(s){' under ' + prefix if prefix else ''}\n{body}{more}")


async def _fetch_blob(full: str, ref: str, path: str) -> tuple[bytes | None, str]:
    rc, out = await _gh("api", f"repos/{full}/contents/{path}?ref={ref}")
    if rc != 0:
        return None, out.strip() or f"gh exited {rc}"
    try:
        meta = json.loads(out)
    except ValueError:
        return None, "unexpected response from GitHub"
    if isinstance(meta, list):
        return None, f"{path} is a directory; name files, or use path_prefix on github_get_tree"
    if meta.get("encoding") == "base64" and meta.get("content") is not None:
        return base64.b64decode(meta["content"]), ""
    if meta.get("size", 0) > _MAX_FILE_BYTES:
        return None, f"{path} is over {_MAX_FILE_BYTES // (1024 * 1024)} MB; not imported"
    return None, f"{path}: no content returned"


@tool(
    "github_read_file",
    "Read one file from a GitHub repository at a ref, without importing it. For a "
    "single-file URL (github.com/OWNER/REPO/blob/REF/PATH). Text only; a binary file "
    "says so.",
    {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"},
                                      "ref": {"type": "string"}, "path": {"type": "string"}},
     "required": ["owner", "repo", "path"]},
)
async def github_read_file(args: dict[str, Any]) -> dict[str, Any]:
    full = _repo(str(args.get("owner") or ""), str(args.get("repo") or ""))
    path = str(args.get("path") or "").strip().strip("/")
    if not full or not path:
        return err("owner, repo, and path are required.")
    ref = str(args.get("ref") or "").strip() or "HEAD"
    data, why = await _fetch_blob(full, ref, path)
    if data is None:
        return err(why)
    if b"\0" in data[:8000]:
        return err(f"{path} is binary ({len(data):,} B); import it with github_import_files instead.")
    text = data.decode("utf-8", "replace")
    if len(text) > 200_000:
        text = text[:200_000] + "\n[...truncated at 200000 chars]"
    return ok(f"--- {full}@{ref}:{path} ---\n{text}")


@tool(
    "github_import_files",
    "Copy files from a GitHub repository into the workspace, keeping their paths, so "
    "they can be read and used as design context. Give the exact file paths from "
    "github_get_tree (up to 60). Imported files land under `<workspace>/<dest>/` "
    "(default: imported/<repo>/). Nothing is executed.",
    {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"},
                                      "ref": {"type": "string"},
                                      "paths": {"type": "array", "items": {"type": "string"}},
                                      "dest": {"type": "string"}},
     "required": ["owner", "repo", "paths"]},
)
async def github_import_files(args: dict[str, Any]) -> dict[str, Any]:
    full = _repo(str(args.get("owner") or ""), str(args.get("repo") or ""))
    if not full:
        return err("owner and repo must be plain GitHub names.")
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
        return err("paths must be a non-empty list of repository file paths.")
    if len(paths) > _MAX_IMPORT_FILES:
        return err(f"at most {_MAX_IMPORT_FILES} files per import; pick the ones that carry the design.")
    ref = str(args.get("ref") or "").strip() or "HEAD"
    ws = ctx().workspace.resolve()
    dest_rel = str(args.get("dest") or f"imported/{full.split('/')[1]}").strip().strip("/")
    dest = (ws / dest_rel).resolve()
    if not dest.is_relative_to(ws):
        return err("dest must be inside the workspace.")
    landed, failed = [], []
    for raw in paths:
        rel = raw.strip().strip("/")
        target = (dest / rel).resolve()
        if ".." in Path(rel).parts or not target.is_relative_to(dest):
            failed.append(f"{rel}: refused (path escapes the import folder)")
            continue
        data, why = await _fetch_blob(full, ref, rel)
        if data is None:
            failed.append(f"{rel}: {why}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        landed.append(f"{target.relative_to(ws)}  ({len(data):,} B)")
    head = f"Imported {len(landed)} of {len(paths)} file(s) from {full}@{ref} into {dest_rel}/"
    body = ("\n" + "\n".join(landed) if landed else "") + ("\nNot imported:\n" + "\n".join(failed) if failed else "")
    if not landed:
        return err(head + body)
    return ok(head + body + "\nNow read_file the ones that carry tokens, components, and global styles.")


GITHUB_TOOLS = [github_list_repos, github_get_tree, github_read_file, github_import_files]
