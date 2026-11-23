"""Unit tests for automated local Docker rollback image retention."""

import json
from pathlib import Path
import runpy
from unittest import mock

import pytest


PRUNE = (
    Path(__file__).resolve().parents[2] / "scripts/deployment/prune_rollback_images.py"
)
retention = runpy.run_path(str(PRUNE))


def _make_release_point(
    root,
    name,
    *,
    image_map,
    verified_at=None,
    commit="abc123456789",
    keep_marker=False,
):
    point = root / name
    point.mkdir(parents=True, exist_ok=True)
    running_images = []
    for svc, iid in image_map.items():
        running_images.append(
            {
                "service": svc,
                "image_id": iid,
                "rollback_tag": f"swimbuddz-rollback:{name}-{svc}",
            }
        )
    (point / "release.json").write_text(
        json.dumps(
            {
                "previous_commit": commit,
                "project": "swimbuddz-backend",
                "running_images": running_images,
            }
        )
    )
    if verified_at:
        db_dir = point / "database"
        db_dir.mkdir(exist_ok=True)
        (db_dir / "database.dump").write_bytes(b"PGDMP-test")
        (db_dir / "verified.json").write_text(json.dumps({"verified_at": verified_at}))
    if keep_marker:
        (point / ".keep-images").write_text("incident pin\n")
    return point


def _build_images_by_id(release_records, extra_tags_by_id=None):
    tags_by_id = {}
    for rec in release_records:
        rel = json.loads((rec["path"] / "release.json").read_text())
        for item in rel["running_images"]:
            tags_by_id.setdefault(item["image_id"], []).append(item["rollback_tag"])
    for iid, tags in (extra_tags_by_id or {}).items():
        tags_by_id.setdefault(iid, []).extend(tags)
    return {iid: {"Id": iid, "RepoTags": tags} for iid, tags in tags_by_id.items()}


def test_retention_keeps_running_canaries_keep_marker_current_and_two_distinct_verified(
    tmp_path,
):
    releases = tmp_path / "releases"
    canaries = tmp_path / "canaries"
    releases.mkdir()
    canaries.mkdir()

    # Active canary pin (single-service image from an older release)
    canary_dir = canaries / "members-20261119T025926Z"
    canary_dir.mkdir()
    (canary_dir / "image-pin.json").write_text(
        json.dumps(
            {
                "services": {
                    "members-service": {
                        "image": "sha256:canary_members_old",
                        "pull_policy": "never",
                    }
                }
            }
        )
    )

    # Release history from oldest to newest
    _make_release_point(
        releases,
        "20261116T090000Z-1",
        image_map={"gateway": "sha256:gw_v1", "members": "sha256:mem_v1"},
        verified_at="2026-11-16T09:10:00Z",
    )
    _make_release_point(
        releases,
        "20261117T090000Z-2",
        image_map={"gateway": "sha256:gw_pin", "members": "sha256:mem_pin"},
        verified_at="2026-11-17T09:10:00Z",
        keep_marker=True,
    )
    _make_release_point(
        releases,
        "20261118T090000Z-3",
        image_map={"gateway": "sha256:gw_v2", "members": "sha256:mem_v2"},
        verified_at="2026-11-18T09:10:00Z",
    )
    # Prev verified #2 (distinct)
    _make_release_point(
        releases,
        "20261119T090000Z-4",
        image_map={"gateway": "sha256:gw_v3", "members": "sha256:mem_v3"},
        verified_at="2026-11-19T09:10:00Z",
    )
    # Prev verified #1 (distinct)
    _make_release_point(
        releases,
        "20261120T090000Z-5",
        image_map={"gateway": "sha256:gw_v4", "members": "sha256:mem_v4"},
        verified_at="2026-11-20T09:10:00Z",
    )
    # Failed unverified deploy attempt (should be retired)
    _make_release_point(
        releases,
        "20261121T090000Z-6",
        image_map={"gateway": "sha256:gw_v4", "members": "sha256:mem_v4"},
        verified_at=None,
    )
    # Verified point matching the currently running stack (kept at 0 extra storage)
    _make_release_point(
        releases,
        "20261123T090000Z-7",
        image_map={"gateway": "sha256:gw_running", "members": "sha256:mem_running"},
        verified_at="2026-11-23T09:10:00Z",
    )
    # Newly captured in-flight unverified point (protected via --current-point)
    current_pt = _make_release_point(
        releases,
        "20261123T120000Z-8",
        image_map={"gateway": "sha256:gw_running", "members": "sha256:mem_running"},
        verified_at=None,
    )

    records = retention["inspect_release_points"](releases)
    canary_ids, canary_tags = retention["collect_canary_protections"](canaries)
    images_by_id = _build_images_by_id(
        records,
        extra_tags_by_id={
            "sha256:canary_members_old": [
                "swimbuddz-rollback:members-20261119T025926Z"
            ],
            "sha256:pg17": ["postgres:17-alpine"],
            "sha256:orphan_latest": ["ugodaniels/swimbuddz-academy-service:latest"],
            "sha256:unrelated_third_party": ["alpine:3.20"],
        },
    )

    plan = retention["compute_retention_plan"](
        running_ids={"sha256:gw_running", "sha256:mem_running"},
        images_by_id=images_by_id,
        release_records=records,
        canary_ids=canary_ids,
        canary_tags=canary_tags,
        keep_verified_sets=2,
        current_point=current_pt,
    )

    assert plan["protected_point_names"] == [
        "20261117T090000Z-2",  # .keep-images marker
        "20261119T090000Z-4",  # prev verified #2
        "20261120T090000Z-5",  # prev verified #1
        "20261123T090000Z-7",  # verified match for running stack
        "20261123T120000Z-8",  # current in-flight point
    ]
    retired_names = sorted(rec["name"] for rec, _ in plan["retired_points"])
    assert retired_names == [
        "20261116T090000Z-1",
        "20261118T090000Z-3",
        "20261121T090000Z-6",
    ]
    assert ("ugodaniels/swimbuddz-academy-service:latest", "sha256:orphan_latest") in (
        plan["orphaned_latest_tags"]
    )
    assert "alpine:3.20" not in plan["tags_to_remove"]
    assert "postgres:17-alpine" not in plan["tags_to_remove"]
    assert "swimbuddz-rollback:members-20261119T025926Z" not in plan["tags_to_remove"]
    assert set(plan["will_delete_ids"]) == {
        "sha256:gw_v1",
        "sha256:mem_v1",
        "sha256:gw_v2",
        "sha256:mem_v2",
        "sha256:orphan_latest",
    }


def test_apply_preserves_db_backups_and_writes_retired_receipt(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()
    old_pt = _make_release_point(
        releases,
        "20261116T090000Z-1",
        image_map={"gateway": "sha256:gw_v1"},
        verified_at="2026-11-16T09:10:00Z",
        commit="deadbeef1234",
    )
    records = retention["inspect_release_points"](releases)
    images_by_id = _build_images_by_id(records)
    plan = retention["compute_retention_plan"](
        running_ids=set(),
        images_by_id=images_by_id,
        release_records=records,
        canary_ids=set(),
        canary_tags=set(),
        keep_verified_sets=0,
    )

    calls = []

    def fake_run(args, *, check=True):
        calls.append(args)
        return mock.Mock(returncode=0, stdout="", stderr="")

    retention["apply_retention_plan"](plan, keep_verified_sets=0, run_docker=fake_run)

    assert calls == [
        ["rmi", "swimbuddz-rollback:20261116T090000Z-1-gateway"],
        ["image", "prune", "-f"],
        ["builder", "prune", "-f"],
    ]
    assert (old_pt / "database" / "database.dump").read_bytes() == b"PGDMP-test"
    assert (old_pt / "database" / "verified.json").exists()
    receipt = json.loads((old_pt / "retired-images.json").read_text())
    assert receipt["previous_commit"] == "deadbeef1234"
    assert receipt["retired_tag_count"] == 1
    assert receipt["database_backup_retained"] is True
    assert receipt["verification_record_retained"] is True
    assert (old_pt / "retired-images.json").stat().st_mode & 0o777 == 0o600


def test_headroom_check_enforces_minimum_free_gib(tmp_path):
    ok_checks = retention["check_free_space"]([str(tmp_path)], min_free_gib=0.0001)
    assert len(ok_checks) == 1
    assert ok_checks[0][2] is True

    low_checks = retention["check_free_space"]([str(tmp_path)], min_free_gib=999_999.0)
    assert len(low_checks) == 1
    assert low_checks[0][2] is False


def test_refuses_to_strip_last_tag_from_protected_non_running_image(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()
    _make_release_point(
        releases,
        "20261116T090000Z-1",
        image_map={"members": "sha256:canary_only"},
        verified_at="2026-11-16T09:10:00Z",
    )
    records = retention["inspect_release_points"](releases)
    images_by_id = _build_images_by_id(records)
    with pytest.raises(RuntimeError, match="Refusing to remove last tag"):
        retention["compute_retention_plan"](
            running_ids=set(),
            images_by_id=images_by_id,
            release_records=records,
            canary_ids={"sha256:canary_only"},
            canary_tags=set(),
            keep_verified_sets=0,
        )


def test_shared_python_base_dockerfile_and_workflow_wiring():
    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    base_dockerfile = (repo_root / "Dockerfile.python-base").read_text()
    assert "pyproject.toml" in base_dockerfile
    assert "constraints.txt" in base_dockerfile
    assert "swimbuddz.pth" in base_dockerfile

    workflow = yaml.safe_load((repo_root / ".github/workflows/deploy.yml").read_text())
    base_job = workflow["jobs"]["build-python-base"]
    assert "deps_hash" in base_job["outputs"]
    build_job = workflow["jobs"]["build-and-push"]
    assert "build-python-base" in build_job["needs"]

    for item in build_job["strategy"]["matrix"]["include"]:
        df = (repo_root / item["dockerfile"]).read_text()
        assert "ARG BASE_IMAGE=deps-base" in df, item["dockerfile"]
        assert "FROM ${BASE_IMAGE} AS runtime" in df, item["dockerfile"]
        assert "constraints.txt" in df, item["dockerfile"]
        assert "swimbuddz.pth" in df, item["dockerfile"]
