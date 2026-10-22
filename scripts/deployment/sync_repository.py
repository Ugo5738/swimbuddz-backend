"""Synchronize a deployment checkout, preserving source drift before replacement."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tarfile
import uuid


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def paths(output: bytes) -> set[str]:
    return {os.fsdecode(value) for value in output.split(b"\0") if value}


def sync_repository(root: Path, branch: str, backup_root: Path | None = None):
    root = root.resolve()
    git(root, "check-ref-format", "--branch", branch)
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()) != root:
        raise RuntimeError("Deployment path must be the repository root")
    target = f"refs/remotes/origin/{branch}"
    target_sha = git(root, "rev-parse", "--verify", f"{target}^{{commit}}").strip()
    tracked = paths(git(root, "ls-files", "-z"))
    changed = paths(git(root, "diff", "--no-renames", "--name-only", "-z", "HEAD"))
    target_files = paths(git(root, "ls-tree", "-r", "--name-only", "-z", target))
    collisions = set()
    for relative in target_files:
        if relative == ".env.prod" or relative.startswith("letsencrypt/"):
            raise RuntimeError(f"Release must not contain runtime file: {relative}")
        if relative in tracked:
            continue
        path = root / relative
        for depth in range(1, len(Path(relative).parts) + 1):
            candidate = root.joinpath(*Path(relative).parts[:depth])
            name = candidate.relative_to(root).as_posix()
            if candidate.is_symlink() or candidate.is_file():
                if name not in tracked:
                    ignored = subprocess.run(
                        ["git", "-C", str(root), "check-ignore", "-q", "--", name]
                    )
                    if ignored.returncode not in (0, 1):
                        raise RuntimeError(f"Could not check ignore rules for {name}")
                    if ignored.returncode == 0:
                        raise RuntimeError(
                            f"Release would overwrite ignored file: {name}"
                        )
                    collisions.add(name)
                break
            if candidate.is_dir():
                if candidate == path and git(
                    root, "ls-files", "--others", "-z", "--", name
                ):
                    raise RuntimeError(
                        f"Release would replace an untracked directory: {name}"
                    )

    backup = None
    if changed or collisions:
        backup_root = (
            backup_root or root.parent / "deployment-backups" / root.name
        ).resolve()
        if backup_root == root or root in backup_root.parents:
            raise RuntimeError("Source backups must be outside the deployment checkout")
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        backup.mkdir(mode=0o700)
        metadata = {
            "head": git(root, "rev-parse", "HEAD").decode().strip(),
            "target": target_sha.decode(),
            "changed": sorted(changed),
            "untracked_collisions": sorted(collisions),
        }
        (backup / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (backup / "tracked.patch").write_bytes(
            git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD")
        )
        with tarfile.open(backup / "source.tar", "x") as archive:
            for relative in sorted(changed | collisions):
                path = root / relative
                if path.exists() or path.is_symlink():
                    archive.add(path, arcname=relative)
        for path in backup.iterdir():
            path.chmod(0o600)
        print(f"Archived deployment source changes: {backup}", flush=True)

    # Force only the source checkout after its conflicting content is durable.
    # Never git-clean: non-conflicting uploads, environment and TLS data remain.
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--force", "-B", branch, target],
        check=True,
    )
    if git(root, "rev-parse", "HEAD").strip() != target_sha:
        raise RuntimeError("Deployment checkout does not match the fetched release")
    subprocess.run(
        ["git", "-C", str(root), "diff", "--exit-code", "HEAD", "--"], check=True
    )
    return backup


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    sync_repository(args.repository, args.branch, args.backup_root)
