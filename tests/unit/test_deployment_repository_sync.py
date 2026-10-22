"""Exercise deployment checkout recovery using real local Git repositories."""

import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tarfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "scripts/deployment/sync_repository.py"
sync_repository = runpy.run_path(str(SYNC))["sync_repository"]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def write(root, name, body):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def repository(tmp_path, release_files=None):
    root = tmp_path / "server"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Deployment test")
    write(root, ".gitignore", ".env.prod\nletsencrypt/\nignored-cache\n")
    write(root, "app.py", "original\n")
    write(root, "removed.py", "original removed file\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "original")
    original = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-qc", "release")
    write(root, "app.py", "released\n")
    write(root, "new_module.py", "released module\n")
    (root / "removed.py").unlink()
    for name, body in (release_files or {}).items():
        write(root, name, body)
    git(root, "add", "--force", ".")
    git(root, "commit", "-qm", "release")
    release = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", release)
    git(root, "switch", "-q", "main")
    return root, original, release


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_checkout_is_archived_and_runtime_files_survive(tmp_path, staged):
    root, original, release = repository(tmp_path)
    write(root, "app.py", "server hotfix\n")
    (root / "removed.py").unlink()
    if staged:
        git(root, "add", "-u")
    write(root, "new_module.py", "untracked hotfix\n")
    write(root, ".env.prod", "private environment\n")
    write(root, "letsencrypt/acme.json", "private certificate\n")
    write(root, "uploads/photo.dat", "runtime upload\n")

    backup = sync_repository(root, "main", tmp_path / "backups")

    assert git(root, "rev-parse", "HEAD") == release
    assert git(root, "diff", "HEAD") == ""
    assert (root / "app.py").read_text() == "released\n"
    assert (root / "new_module.py").read_text() == "released module\n"
    assert not (root / "removed.py").exists()
    assert (root / ".env.prod").read_text() == "private environment\n"
    assert (root / "letsencrypt/acme.json").read_text() == "private certificate\n"
    assert (root / "uploads/photo.dat").read_text() == "runtime upload\n"
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["head"] == original and manifest["target"] == release
    assert manifest["untracked_collisions"] == ["new_module.py"]
    assert "server hotfix" in (backup / "tracked.patch").read_text()
    with tarfile.open(backup / "source.tar") as archive:
        assert archive.extractfile("app.py").read() == b"server hotfix\n"
        assert archive.extractfile("new_module.py").read() == b"untracked hotfix\n"
        assert set(archive.getnames()) == {"app.py", "new_module.py"}
    assert backup.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in backup.iterdir())
    assert sync_repository(root, "main", tmp_path / "backups") is None


def test_backup_failure_prevents_checkout(tmp_path):
    root, original, _ = repository(tmp_path)
    write(root, "app.py", "server hotfix\n")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    with pytest.raises(FileExistsError):
        sync_repository(root, "main", blocked)
    assert git(root, "rev-parse", "HEAD") == original
    assert (root / "app.py").read_text() == "server hotfix\n"


def test_ignored_collision_is_preserved_and_checkout_refused(tmp_path):
    root, original, _ = repository(tmp_path, {"ignored-cache": "release cache"})
    write(root, "ignored-cache", "private runtime cache")
    with pytest.raises(RuntimeError, match="overwrite ignored file"):
        sync_repository(root, "main")
    assert git(root, "rev-parse", "HEAD") == original
    assert (root / "ignored-cache").read_text() == "private runtime cache"


def test_untracked_directory_is_never_removed_for_a_release_file(tmp_path):
    root, original, _ = repository(tmp_path, {"bundle": "released file"})
    write(root, "bundle/user-data", "preserve")
    with pytest.raises(RuntimeError, match="untracked directory"):
        sync_repository(root, "main")
    assert git(root, "rev-parse", "HEAD") == original
    assert (root / "bundle/user-data").read_text() == "preserve"


def test_untracked_parent_symlink_is_archived_without_following_it(tmp_path):
    root, _, release = repository(tmp_path, {"package/module.py": "released"})
    outside = tmp_path / "runtime-data"
    outside.mkdir()
    write(outside, "module.py", "private external data")
    (root / "package").symlink_to(outside, target_is_directory=True)
    backup = sync_repository(root, "main")
    assert git(root, "rev-parse", "HEAD") == release
    assert (outside / "module.py").read_text() == "private external data"
    assert (root / "package/module.py").read_text() == "released"
    with tarfile.open(backup / "source.tar") as archive:
        assert archive.getmember("package").issym()
        assert archive.getnames() == ["package"]


def test_ssh_bootstrap_loads_release_scripts_before_syncing_dirty_checkout(tmp_path):
    # The old bootstrap failed at checkout before deploy-production.sh could
    # recover. Run the real workflow/deploy scripts with local Git and fake
    # Docker/GPG binaries, including migration-before-start ordering.
    scripts = ROOT / "scripts/deployment"
    root, _, release = repository(
        tmp_path,
        {
            "scripts/deployment/deploy-production.sh": (
                scripts / "deploy-production.sh"
            ).read_text(),
            "scripts/deployment/_common.sh": (scripts / "_common.sh").read_text(),
            "scripts/deployment/sync_repository.py": SYNC.read_text(),
            "scripts/deployment/verify-production.sh": '#!/usr/bin/env bash\nprintf verified > "$DEPLOY_PATH/verification-result"\n',
            ".env.prod.gpg": "encrypted fixture",
        },
    )
    git(root, "config", "core.fileMode", "true")
    git(root, "switch", "-q", "release")
    (root / "scripts/deployment/verify-production.sh").chmod(0o755)
    git(root, "add", "scripts/deployment/verify-production.sh")
    git(root, "commit", "-qm", "executable verifier")
    release = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(root), str(remote)],
        check=True,
        capture_output=True,
    )
    git(remote, "update-ref", "refs/heads/main", release)
    git(root, "remote", "add", "origin", str(remote))
    write(root, "app.py", "server hotfix\n")
    write(root, "new_module.py", "untracked hotfix\n")
    write(root, "scripts/deployment/deploy-production.sh", "exit 99\n")
    binaries = tmp_path / "bin"
    write(
        binaries,
        "docker",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$DEPLOYMENT_DOCKER_LOG"\nif [ "$1" = login ]; then cat > /dev/null; fi\n',
    )
    write(
        binaries,
        "gpg",
        '#!/usr/bin/env bash\ncat > /dev/null\nwhile [ "$1" != --output ]; do shift; done\nshift\nprintf "ENVIRONMENT=production\\n" > "$1"\n',
    )
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    docker_log = tmp_path / "docker.log"
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    bootstrap = workflow["jobs"]["deploy"]["steps"][0]["with"]["script"]
    bootstrap = bootstrap.replace(
        "export DEPLOY_PATH=/home/deploy/swimbuddz-backend",
        f"export DEPLOY_PATH={shlex.quote(str(root))}",
    )
    subprocess.run(
        ["bash", "-c", bootstrap],
        env={
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "DEPLOY_BRANCH": "main",
            "REPO_URL": str(remote),
            "GPG_PASSPHRASE": "test-only",
            "DOCKER_USERNAME": "test-only",
            "DOCKER_PASSWORD": "test-only",
            "LE_EMAIL": "test@example.invalid",
            "DEPLOYMENT_DOCKER_LOG": str(docker_log),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert git(root, "rev-parse", "HEAD") == release
    assert (root / "verification-result").read_text() == "verified"
    assert (root / "app.py").read_text() == "released\n"
    assert (root / ".env.prod").stat().st_mode & 0o777 == 0o600
    commands = docker_log.read_text().splitlines()
    migrate = next(i for i, line in enumerate(commands) if "migrate-prod.sh" in line)
    start = next(i for i, line in enumerate(commands) if " up -d --wait" in line)
    assert migrate < start
