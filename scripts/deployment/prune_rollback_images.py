#!/usr/bin/env python3
"""Retire expired local Docker rollback tags while preserving DB backups and pins.

Separates database/manifest retention (never deleted here) from local Docker
image retention. Always protects:
  1. Images used by any existing container (`docker ps -aq`)
  2. The in-flight deployment's just-captured recovery point (`--current-point`)
  3. Active canary / incident pins under `--canaries-root` and any release point
     containing a `.keep-images` marker file
  4. Required infrastructure images (`postgres:17-alpine`, `redis:7-alpine`,
     `traefik:v3`)
  5. The latest N distinct non-running verified rollback image sets
     (`--keep-verified-sets`, default 2) plus the newest verified point matching
     the currently running stack
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DEFAULT_INFRA_TAGS = frozenset(
    {
        "postgres:17-alpine",
        "redis:7-alpine",
        "traefik:v3",
    }
)
ORPHANED_APP_LATEST_RE = re.compile(r"^[^/]+/swimbuddz-[^:]+:latest$")
ROLLBACK_PREFIX = "swimbuddz-rollback:"


def _run_docker(args, *, check=True):
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def load_docker_state():
    """Return (running_image_ids, images_by_id) from local Docker daemon."""
    ps_out = _run_docker(["ps", "-aq"]).stdout.split()
    running_ids = set()
    if ps_out:
        raw = _run_docker(["inspect", *ps_out]).stdout.strip()
        if raw:
            for container in json.loads(raw):
                image_id = container.get("Image")
                if image_id:
                    running_ids.add(image_id)

    img_ids = sorted(set(_run_docker(["images", "-aq", "--no-trunc"]).stdout.split()))
    images_by_id = {}
    if img_ids:
        raw = _run_docker(["inspect", *img_ids]).stdout.strip()
        if raw:
            for image in json.loads(raw):
                images_by_id[image["Id"]] = image

    return running_ids, images_by_id


def _load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _verified_sort_key(point_dir, verified_data):
    timestamp = (
        (verified_data or {}).get("verified_at")
        or (verified_data or {}).get("verified_at_utc")
        or ""
    )
    return (timestamp, point_dir.name)


def collect_canary_protections(canaries_root):
    """Return (protected_image_ids, protected_tags) from canary manifests."""
    protected_ids = set()
    protected_tags = set()
    root = Path(canaries_root)
    if not root.is_dir():
        return protected_ids, protected_tags

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        protected_tags.add(f"{ROLLBACK_PREFIX}{entry.name}")
        pin_data = _load_json(entry / "image-pin.json")
        if not isinstance(pin_data, dict):
            continue
        for service_cfg in (pin_data.get("services") or {}).values():
            if not isinstance(service_cfg, dict):
                continue
            image = service_cfg.get("image")
            if not image:
                continue
            if image.startswith("sha256:"):
                protected_ids.add(image)
            else:
                protected_tags.add(image)
    return protected_ids, protected_tags


def inspect_release_points(recovery_root):
    """Return metadata records for every release directory under recovery_root."""
    root = Path(recovery_root)
    if not root.is_dir():
        return []

    records = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        rel_data = _load_json(entry / "release.json")
        ver_data = _load_json(entry / "database" / "verified.json")
        running_images = (
            rel_data.get("running_images", []) if isinstance(rel_data, dict) else []
        )
        image_ids = frozenset(
            item["image_id"]
            for item in running_images
            if isinstance(item, dict) and item.get("image_id")
        )
        rollback_tags = [
            item["rollback_tag"]
            for item in running_images
            if isinstance(item, dict) and item.get("rollback_tag")
        ]
        records.append(
            {
                "path": entry.resolve(),
                "name": entry.name,
                "verified": bool(ver_data),
                "verified_sort_key": _verified_sort_key(entry, ver_data),
                "keep_marker": (entry / ".keep-images").exists(),
                "commit": (rel_data or {}).get("previous_commit"),
                "image_ids": image_ids,
                "rollback_tags": rollback_tags,
            }
        )
    return records


def compute_retention_plan(
    *,
    running_ids,
    images_by_id,
    release_records,
    canary_ids,
    canary_tags,
    keep_verified_sets=2,
    current_point=None,
    infra_tags=DEFAULT_INFRA_TAGS,
):
    """Compute which release points stay protected and which Docker tags can be retired."""
    current_resolved = Path(current_point).resolve() if current_point else None
    present_ids = set(images_by_id.keys())
    tag_to_id = {}
    for iid, info in images_by_id.items():
        for tag in info.get("RepoTags") or []:
            tag_to_id[tag] = iid

    protected_point_names = set()
    # 1. Always protect the current in-flight point and any explicit .keep-images point.
    for rec in release_records:
        if current_resolved and rec["path"] == current_resolved:
            protected_point_names.add(rec["name"])
        elif rec["keep_marker"]:
            protected_point_names.add(rec["name"])

    # 2. Walk verified points newest-first, keeping the newest point matching the
    #    running stack (zero extra storage) plus `keep_verified_sets` distinct
    #    non-running verified image sets that still have local images present.
    verified_newest_first = sorted(
        (rec for rec in release_records if rec["verified"]),
        key=lambda rec: rec["verified_sort_key"],
        reverse=True,
    )
    running_sig = frozenset(running_ids)
    kept_running_match = False
    kept_distinct_non_running_sigs = set()

    for rec in verified_newest_first:
        present_point_tags = [t for t in rec["rollback_tags"] if t in tag_to_id]
        if not present_point_tags:
            continue
        sig = rec["image_ids"] or frozenset(tag_to_id[t] for t in present_point_tags)
        if not sig:
            continue
        if running_sig and sig == running_sig:
            if not kept_running_match:
                protected_point_names.add(rec["name"])
                kept_running_match = True
            continue
        if sig in kept_distinct_non_running_sigs:
            continue
        if len(kept_distinct_non_running_sigs) < max(0, keep_verified_sets):
            protected_point_names.add(rec["name"])
            kept_distinct_non_running_sigs.add(sig)

    # 3. Build complete protected image ID and tag sets.
    protected_ids = set(running_ids) | set(canary_ids)
    protected_tags = set(canary_tags) | set(infra_tags)

    for tag in infra_tags:
        if tag in tag_to_id:
            protected_ids.add(tag_to_id[tag])
    for tag in canary_tags:
        if tag in tag_to_id:
            protected_ids.add(tag_to_id[tag])

    for rec in release_records:
        if rec["name"] not in protected_point_names:
            continue
        protected_ids.update(rec["image_ids"])
        for tag in rec["rollback_tags"]:
            protected_tags.add(tag)
            if tag in tag_to_id:
                protected_ids.add(tag_to_id[tag])

    # 4. Collect rollback tags from retired / unprotected release points.
    tags_to_remove = []
    seen_remove = set()
    retired_points = []
    for rec in release_records:
        if rec["name"] in protected_point_names:
            continue
        pt_tags = [
            tag
            for tag in rec["rollback_tags"]
            if tag in tag_to_id and tag not in protected_tags
        ]
        if pt_tags:
            retired_points.append((rec, pt_tags))
            for tag in pt_tags:
                if tag not in seen_remove:
                    seen_remove.add(tag)
                    tags_to_remove.append(tag)

    # 5. Also clean up orphaned non-running app `:latest` leftovers from failed pulls.
    orphaned_latest_tags = []
    for iid, info in images_by_id.items():
        if iid in protected_ids:
            continue
        for tag in info.get("RepoTags") or []:
            if tag in protected_tags or tag in seen_remove:
                continue
            if ORPHANED_APP_LATEST_RE.match(tag):
                seen_remove.add(tag)
                tags_to_remove.append(tag)
                orphaned_latest_tags.append((tag, iid))

    # 6. Safety check: never strip the last tag from a protected non-running image.
    for iid in sorted(protected_ids & present_ids):
        if iid in running_ids:
            continue
        repo_tags = images_by_id[iid].get("RepoTags") or []
        surviving = [tag for tag in repo_tags if tag not in seen_remove]
        if repo_tags and not surviving:
            raise RuntimeError(
                f"Refusing to remove last tag on protected non-running image {iid}"
            )

    will_delete_ids = []
    for iid, info in images_by_id.items():
        if iid in protected_ids:
            continue
        repo_tags = info.get("RepoTags") or []
        surviving = [tag for tag in repo_tags if tag not in seen_remove]
        if not surviving:
            will_delete_ids.append(iid)

    return {
        "protected_point_names": sorted(protected_point_names),
        "protected_ids": protected_ids,
        "retired_points": retired_points,
        "orphaned_latest_tags": sorted(orphaned_latest_tags),
        "tags_to_remove": tags_to_remove,
        "will_delete_ids": sorted(will_delete_ids),
    }


def check_free_space(paths, min_free_gib):
    """Return list of (path, free_gib, ok) for each existing unique filesystem."""
    results = []
    seen_devs = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        st = os.stat(path)
        if st.st_dev in seen_devs:
            continue
        seen_devs.add(st.st_dev)
        vfs = os.statvfs(path)
        free_gib = (vfs.f_bavail * vfs.f_frsize) / (1024**3)
        results.append((str(path), free_gib, free_gib >= min_free_gib))
    return results


def apply_retention_plan(plan, *, keep_verified_sets, run_docker=_run_docker):
    tags = plan["tags_to_remove"]
    for idx in range(0, len(tags), 40):
        batch = tags[idx : idx + 40]
        run_docker(["rmi", *batch], check=True)

    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for rec, pt_tags in plan["retired_points"]:
        if not rec["verified"]:
            continue
        receipt_path = rec["path"] / "retired-images.json"
        if receipt_path.exists():
            continue
        receipt = {
            "retired_at_utc": now_utc,
            "reason": (
                f"Automated rollback image retention (keep running + active pins + "
                f"{keep_verified_sets} distinct verified rollback image sets)"
            ),
            "previous_commit": rec["commit"],
            "retired_tag_count": len(pt_tags),
            "database_backup_retained": (
                rec["path"] / "database" / "database.dump"
            ).exists(),
            "verification_record_retained": (
                rec["path"] / "database" / "verified.json"
            ).exists(),
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        receipt_path.chmod(0o600)

    run_docker(["image", "prune", "-f"], check=True)
    run_docker(["builder", "prune", "-f"], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recovery-root",
        default=os.environ.get(
            "RECOVERY_ROOT", "/home/deploy/operational-backups/releases"
        ),
    )
    parser.add_argument("--canaries-root", default=None)
    parser.add_argument(
        "--keep-verified-sets",
        type=int,
        default=int(os.environ.get("ROLLBACK_IMAGE_RETENTION_COUNT", "2")),
    )
    parser.add_argument("--current-point", default=None)
    parser.add_argument("--min-free-gib", type=float, default=0.0)
    parser.add_argument("--check-path", action="append", default=[])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute docker rmi and prune (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print retention plan without removing any Docker images.",
    )
    args = parser.parse_args(argv)

    recovery_root = Path(args.recovery_root)
    canaries_root = (
        Path(args.canaries_root)
        if args.canaries_root
        else recovery_root.parent / "canaries"
    )

    running_ids, images_by_id = load_docker_state()
    canary_ids, canary_tags = collect_canary_protections(canaries_root)
    release_records = inspect_release_points(recovery_root)

    plan = compute_retention_plan(
        running_ids=running_ids,
        images_by_id=images_by_id,
        release_records=release_records,
        canary_ids=canary_ids,
        canary_tags=canary_tags,
        keep_verified_sets=args.keep_verified_sets,
        current_point=args.current_point,
    )

    mode = "APPLY" if (args.apply and not args.dry_run) else "DRY-RUN"
    print(
        f"[rollback-retention:{mode}] protected_points={plan['protected_point_names']} "
        f"protected_images={len(plan['protected_ids'])} "
        f"retired_points={len(plan['retired_points'])} "
        f"orphaned_latest={len(plan['orphaned_latest_tags'])} "
        f"tags_to_remove={len(plan['tags_to_remove'])} "
        f"images_to_reclaim={len(plan['will_delete_ids'])}"
    )

    if args.apply and not args.dry_run:
        apply_retention_plan(plan, keep_verified_sets=args.keep_verified_sets)

    if args.min_free_gib > 0:
        check_paths = args.check_path or [str(recovery_root.parent)]
        checks = check_free_space(check_paths, args.min_free_gib)
        failed = []
        for path, free_gib, ok in checks:
            status = "OK" if ok else "LOW"
            print(
                f"[disk-headroom:{status}] {path}: {free_gib:.2f} GiB free "
                f"(required >= {args.min_free_gib:.2f} GiB)"
            )
            if not ok:
                failed.append((path, free_gib))
        if failed:
            print(
                "ERROR: Insufficient free disk space for safe deployment pull. "
                "See docs/operations/PRODUCTION_DATABASE_RECOVERY.md.",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
