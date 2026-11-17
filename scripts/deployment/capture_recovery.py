#!/usr/bin/env python3
"""Capture private runtime configuration and pin running images before a release."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def capture(repository, destination, project, compose_file):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    repository = Path(repository).resolve()
    for name in (".env.prod", ".env", compose_file):
        path = repository / name
        if path.is_file():
            target = destination / path.name
            shutil.copyfile(path, target)
            target.chmod(0o600)
    # Compose interpolation lives in the invoking shell, independently of the
    # containers' .env.prod. Preserve the non-secret deployment values as well.
    interpolation_keys = (
        "DOCKER_USERNAME",
        "LE_EMAIL",
        "API_HOST",
        "COMPOSE_PARALLEL_LIMIT",
        "MEDIA_WORKER_DB_USE_LOCAL_POOL",
        "MEDIA_WORKER_DB_POOL_SIZE",
        "MEDIA_WORKER_DB_MAX_OVERFLOW",
        "MEMBERS_API_DB_USE_LOCAL_POOL",
        "CHAT_API_DB_USE_LOCAL_POOL",
    )
    interpolation = {}
    for key in interpolation_keys:
        if key in os.environ:
            value = os.environ[key]
            if "\n" in value or "\r" in value or "'" in value:
                raise ValueError("Invalid Compose interpolation value")
            interpolation[key] = value
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        text=True,
    ).split()
    containers = (
        json.loads(subprocess.check_output(["docker", "inspect", *ids])) if ids else []
    )
    services = {}
    images = []
    for container in containers:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        runtime_env = dict(
            value.split("=", 1)
            for value in container["Config"].get("Env", [])
            if "=" in value
        )
        switch = {
            "members-service": "MEMBERS_API_DB_USE_LOCAL_POOL",
            "chat-service": "CHAT_API_DB_USE_LOCAL_POOL",
            "media-worker": "MEDIA_WORKER_DB_USE_LOCAL_POOL",
        }.get(service)
        if switch:
            # The new deployment's shell may already request a different flag.
            # Rollback must capture the old *running* state, not that request.
            interpolation[switch] = (
                "true"
                if runtime_env.get("DB_USE_LOCAL_POOL", "false").lower()
                in {"true", "1", "yes", "on"}
                else "false"
            )
        if service == "media-worker":
            for setting in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW"):
                value = runtime_env.get(setting)
                if value is not None and value.isdigit():
                    interpolation["MEDIA_WORKER_" + setting] = value
        tag = f"swimbuddz-rollback:{destination.name}-{service}"
        subprocess.run(["docker", "tag", container["Image"], tag], check=True)
        services[service] = {"image": tag, "pull_policy": "never"}
        images.append(
            {"service": service, "image_id": container["Image"], "rollback_tag": tag}
        )
    (destination / "compose-shell.env").write_text(
        "".join(f"{key}='{value}'\n" for key, value in interpolation.items())
    )
    previous = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
    )
    (destination / "release.json").write_text(
        json.dumps(
            {
                "previous_commit": previous.stdout.strip()
                if previous.returncode == 0
                else None,
                "project": project,
                "running_images": images,
            },
            indent=2,
        )
        + "\n"
    )
    (destination / "rollback-images.json").write_text(
        json.dumps({"services": services}, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--compose-file", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    capture(args.repository, args.destination, args.project, args.compose_file)
