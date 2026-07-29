import json
from pathlib import Path

import pytest

from coal_platform.signoff import file_sha256, verify_production_signoff


def write_record(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def acceptance_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    release_uat = tmp_path / "release-uat.json"
    rollback_uat = tmp_path / "rollback-uat.json"
    uat_result = {"status": "passed", "mode": "full-audit", "checks": [{"status": "passed"}]}
    release_uat.write_text(json.dumps(uat_result), encoding="utf-8")
    rollback_uat.write_text(json.dumps(uat_result), encoding="utf-8")

    candidate_commit = "a" * 40
    previous_commit = "b" * 40
    candidate_api = f"registry.test/api@sha256:{'1' * 64}"
    candidate_web = f"registry.test/web@sha256:{'2' * 64}"
    previous_api = f"registry.test/api@sha256:{'3' * 64}"
    previous_web = f"registry.test/web@sha256:{'4' * 64}"
    backup = "/secure/backups/release-1.tar.gz.age"

    release_record = tmp_path / "release.env"
    write_record(
        release_record,
        {
            "status": "passed",
            "candidate_commit": candidate_commit,
            "api_image": candidate_api,
            "web_image": candidate_web,
            "backup": backup,
            "uat_mode": "full",
            "uat_result": str(release_uat),
        },
    )
    rollback_record = tmp_path / "rollback.env"
    write_record(
        rollback_record,
        {
            "status": "passed",
            "candidate_commit": candidate_commit,
            "previous_commit": previous_commit,
            "candidate_api_image": candidate_api,
            "candidate_web_image": candidate_web,
            "previous_api_image": previous_api,
            "previous_web_image": previous_web,
            "backup": backup,
            "image_pull": "immutable-digest",
            "uat_mode": "full",
            "uat_result": str(rollback_uat),
        },
    )
    signoff_path = tmp_path / "signoff.json"
    signoff = {
        "candidate_commit": candidate_commit,
        "environment": "production-cn-north",
        "host_specification": "3 nodes, 8 CPU, 32 GiB RAM",
        "test_window": {
            "started_at": "2026-07-29T09:00:00+08:00",
            "ended_at": "2026-07-29T12:00:00+08:00",
        },
        "security_scan_result": {"status": "passed", "evidence": "security-report-1"},
        "load_test_result": {"status": "passed", "evidence": "load-test-1"},
        "backup_restore_result": {"status": "passed", "evidence": "RPO 0, RTO 39s"},
        "open_defects": {"blocking": 0, "high": 0, "accepted_medium": []},
        "release_record_sha256": file_sha256(release_record),
        "release_uat_sha256": file_sha256(release_uat),
        "rollback_record_sha256": file_sha256(rollback_record),
        "rollback_uat_sha256": file_sha256(rollback_uat),
        "business_signoff": {
            "name": "Business Owner",
            "role": "business",
            "decision": "approved",
            "signed_at": "2026-07-29T12:10:00+08:00",
        },
        "operations_signoff": {
            "name": "Operations Owner",
            "role": "operations",
            "decision": "approved",
            "signed_at": "2026-07-29T12:15:00+08:00",
        },
    }
    signoff_path.write_text(json.dumps(signoff), encoding="utf-8")
    return release_record, rollback_record, signoff_path


def test_production_signoff_verifies_versioned_hashed_and_dually_approved_evidence(tmp_path: Path) -> None:
    release_record, rollback_record, signoff_path = acceptance_evidence(tmp_path)

    result = verify_production_signoff(release_record, rollback_record, signoff_path)

    assert result["status"] == "passed"
    assert result["candidate_commit"] == "a" * 40
    assert result["previous_commit"] == "b" * 40
    assert result["business_signoff"]["decision"] == "approved"
    assert result["operations_signoff"]["decision"] == "approved"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("release_record_sha256", "0" * 64, "does not match"),
        ("environment", "   ", "must be non-empty text"),
        (
            "security_scan_result",
            {"status": "failed", "evidence": "security-report-1"},
            "status must be passed",
        ),
        (
            "test_window",
            {
                "started_at": "2026-07-29T12:00:00+08:00",
                "ended_at": "2026-07-29T09:00:00+08:00",
            },
            "must be after",
        ),
        ("open_defects", {"blocking": 0, "high": 1}, "zero blocking and high"),
        (
            "business_signoff",
            {
                "name": "Business Owner",
                "role": "business",
                "decision": "rejected",
                "signed_at": "2026-07-29T12:10:00+08:00",
            },
            "decision must be approved",
        ),
    ],
)
def test_production_signoff_rejects_unbound_defective_or_unapproved_evidence(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    release_record, rollback_record, signoff_path = acceptance_evidence(tmp_path)
    signoff = json.loads(signoff_path.read_text(encoding="utf-8"))
    signoff[field] = value
    signoff_path.write_text(json.dumps(signoff), encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=message):
        verify_production_signoff(release_record, rollback_record, signoff_path)


def test_production_signoff_rejects_local_drill_as_formal_rollback(tmp_path: Path) -> None:
    release_record, rollback_record, signoff_path = acceptance_evidence(tmp_path)
    rollback = rollback_record.read_text(encoding="utf-8").replace(
        "image_pull=immutable-digest", "image_pull=skipped-local-drill"
    )
    rollback_record.write_text(rollback, encoding="utf-8")

    with pytest.raises(ValueError, match="immutable digest"):
        verify_production_signoff(release_record, rollback_record, signoff_path)


def test_production_signoff_rejects_passed_uat_with_failed_internal_check(tmp_path: Path) -> None:
    release_record, rollback_record, signoff_path = acceptance_evidence(tmp_path)
    release_uat = tmp_path / "release-uat.json"
    release_uat.write_text(
        json.dumps({"status": "passed", "mode": "full-audit", "checks": [{"status": "failed"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only passed acceptance checks"):
        verify_production_signoff(release_record, rollback_record, signoff_path)
