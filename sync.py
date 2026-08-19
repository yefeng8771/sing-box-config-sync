#!/usr/bin/env python3
"""Multi-source sync: pull partial files from GitHub repos / Gists into sources/.

Reads sources.json, fetches each source via `git` sparse-checkout (for repo
sub-directories) or the GitHub Gist API (for gists), writes the results into
sources/<name>/, and records each source's commit/version into .sync-meta.json.

This script ONLY synchronizes files and metadata. It does NOT git commit — the
CI workflow is responsible for committing & pushing any detected changes.

Source types:
  - "github": clone repo@branch with --sparse, checkout only `path`, copy to `dest`
  - "gist"  : fetch gist via API, write each file into `dest`
"""
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
META_FILE = ROOT / ".sync-meta.json"
_CTX = ssl.create_default_context()


def _http_get(url, token=None, accept="application/vnd.github+json", anon_on_auth_error=False):
    """GET a URL and return bytes.

    If anon_on_auth_error is set and the token yields 401/403, retry
    anonymously. Public gists / API resources are readable without auth; the
    Actions-injected GITHUB_TOKEN has no `gist` scope and returns 403 on gists.
    """
    headers = {"User-Agent": "sing-box-config-sync", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180, context=_CTX) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if anon_on_auth_error and token and exc.code in (401, 403):
            print(f"  (token auth got {exc.code}; retrying anonymously)")
            anon_req = urllib.request.Request(url, headers={
                "User-Agent": "sing-box-config-sync", "Accept": accept,
            })
            with urllib.request.urlopen(anon_req, timeout=180, context=_CTX) as resp:
                return resp.read()
        raise


def _run(cmd, cwd=None):
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"  STDOUT: {proc.stdout}\n  STDERR: {proc.stderr}\n")
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.stdout


def safe_replace(src_dir: Path, dest: Path):
    """Atomically replace `dest` with contents of `src_dir`.

    Copies into a sibling temp dir first, so a failure mid-copy leaves the
    existing `dest` untouched.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.parent / (dest.name + ".sync-tmp")
    if tmp_dest.exists():
        shutil.rmtree(tmp_dest)
    # Always exclude .git: for whole-repo mirrors (empty `path`) the source
    # dir is the clone root and would otherwise copy the upstream's .git.
    shutil.copytree(src_dir, tmp_dest, ignore=shutil.ignore_patterns(".git"))
    if dest.exists():
        shutil.rmtree(dest)
    tmp_dest.rename(dest)


def sync_github(src, token):
    repo = src["repo"]
    branch = src["branch"]
    path = src.get("path", "").strip("/")
    dest = ROOT / src["dest"]
    name = src["name"]
    work = ROOT / f".tmp-{name}"
    if work.exists():
        shutil.rmtree(work)
    url = f"https://github.com/{repo}.git"
    # --filter=blob:none + --depth=1 + --sparse: minimal download, only the path we need
    _run(["git", "clone", "--filter=blob:none", "--sparse", "--depth=1",
          "-b", branch, url, str(work)])
    if path:
        _run(["git", "-C", str(work), "sparse-checkout", "set", path])
    else:
        # path empty => mirror the whole branch tree: drop sparse so every
        # blob/dir is materialized (clone used --filter=blob:none + --sparse).
        _run(["git", "-C", str(work), "sparse-checkout", "disable"])
    commit = _run(["git", "-C", str(work), "rev-parse", "HEAD"]).strip()
    src_dir = work / path if path else work
    if not src_dir.exists():
        raise RuntimeError(f"path '{path}' not found in {repo}@{branch}")
    file_count = sum(1 for _ in src_dir.rglob("*")
                     if _.is_file() and ".git" not in _.parts)
    safe_replace(src_dir, dest)
    shutil.rmtree(work, ignore_errors=True)
    tree_url = f"https://github.com/{repo}/tree/{branch}"
    if path:
        tree_url += f"/{path}"
    return {
        "type": "github", "repo": repo, "branch": branch, "path": path,
        "commit": commit, "files": file_count, "url": tree_url,
    }


def sync_gist(src, token):
    gid = src["gist_id"]
    dest = ROOT / src["dest"]
    data = json.loads(_http_get(f"https://api.github.com/gists/{gid}", token,
                                anon_on_auth_error=True))
    tmp = ROOT / f".tmp-{src['name']}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    files = data.get("files", {})
    if not files:
        raise RuntimeError(f"gist {gid} has no files")
    for fname, finfo in files.items():
        content = finfo.get("content")
        if content is None or finfo.get("truncated"):
            raw = _http_get(finfo["raw_url"], token, accept="text/plain",
                            anon_on_auth_error=True)
            (tmp / fname).write_bytes(raw)
        else:
            (tmp / fname).write_text(content, encoding="utf-8")
    safe_replace(tmp, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    version = (data.get("history") or [{}])[0].get("version", "unknown")
    return {
        "type": "gist", "gist_id": gid,
        "version": version, "files": len(files),
        "url": f"https://gist.github.com/{gid}",
    }


def main():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        print("Using GITHUB_TOKEN for API auth (rate-limit friendly).")
    else:
        print("No GITHUB_TOKEN set; using anonymous access (rate-limited).")
    config = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    old_meta = {}
    if META_FILE.exists():
        old_meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    new_meta = {}
    failures = 0
    for src in config["sources"]:
        print(f"\n=== [{src['name']}] type={src['type']} ===")
        try:
            if src["type"] == "github":
                info = sync_github(src, token)
                print(f"  OK  commit={info['commit'][:12]}  files={info['files']}")
            elif src["type"] == "gist":
                info = sync_gist(src, token)
                print(f"  OK  version={info['version'][:12]}  files={info['files']}")
            else:
                raise ValueError(f"unknown source type: {src['type']}")
            new_meta[src["name"]] = info
        except Exception as exc:  # noqa: BLE001
            print(f"  !! FAILED: {exc}")
            failures += 1
            # Keep prior meta unchanged so a failing source does not spuriously
            # trigger a commit (its dest files are also left untouched).
            new_meta[src["name"]] = old_meta.get(src["name"], {})
    META_FILE.write_text(
        json.dumps(new_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if failures:
        print(f"\nSync finished with {failures} failure(s).")
        return 1
    print("\nSync finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
