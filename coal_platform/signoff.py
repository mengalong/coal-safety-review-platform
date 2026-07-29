from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_PATTERN = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_record(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number} is not a key=value record")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise ValueError(f"{path}:{line_number} contains an empty or duplicate key")
        values[key] = value
    return values


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def require_value(mapping: dict[str, Any], key: str, source: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "" or value == [] or value == {}:
        raise ValueError(f"{source}.{key} is required")
    return value


def require_text(mapping: dict[str, Any], key: str, source: str) -> str:
    value = require_value(mapping, key, source)
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{source}.{key} must be non-empty text")
    return value.strip()


def resolve_evidence_path(record_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute() or candidate.is_file():
        return candidate
    sibling = record_path.parent / candidate.name
    if sibling.is_file():
        return sibling
    raise ValueError(f"referenced evidence file does not exist: {raw_path}")


def validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def validate_test_window(signoff: dict[str, Any]) -> dict[str, str]:
    window = require_value(signoff, "test_window", "signoff")
    if not isinstance(window, dict):
        raise TypeError("signoff.test_window must be an object")
    started_at = validate_timestamp(window.get("started_at"), "signoff.test_window.started_at")
    ended_at = validate_timestamp(window.get("ended_at"), "signoff.test_window.ended_at")
    if datetime.fromisoformat(ended_at) <= datetime.fromisoformat(started_at):
        raise ValueError("signoff.test_window.ended_at must be after started_at")
    return {"started_at": started_at, "ended_at": ended_at}


def validate_passed_gate(signoff: dict[str, Any], key: str) -> dict[str, str]:
    gate = require_value(signoff, key, "signoff")
    if not isinstance(gate, dict):
        raise TypeError(f"signoff.{key} must be an object")
    status = require_text(gate, "status", f"signoff.{key}")
    evidence = require_text(gate, "evidence", f"signoff.{key}")
    if status != "passed":
        raise ValueError(f"signoff.{key}.status must be passed")
    return {"status": status, "evidence": evidence}


def validate_approval(signoff: dict[str, Any], key: str) -> dict[str, str]:
    approval = require_value(signoff, key, "signoff")
    if not isinstance(approval, dict):
        raise TypeError(f"signoff.{key} must be an object")
    result = {
        "name": require_text(approval, "name", f"signoff.{key}"),
        "role": require_text(approval, "role", f"signoff.{key}"),
        "decision": require_text(approval, "decision", f"signoff.{key}"),
        "signed_at": validate_timestamp(approval.get("signed_at"), f"signoff.{key}.signed_at"),
    }
    if result["decision"] != "approved":
        raise ValueError(f"signoff.{key}.decision must be approved")
    return result


def validate_uat(path: Path, label: str) -> dict[str, Any]:
    result = read_json(path)
    if result.get("status") != "passed" or result.get("mode") != "full-audit":
        raise ValueError(f"{label} must be a passed full-audit UAT result")
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(check, dict) or check.get("status") != "passed" for check in checks
    ):
        raise ValueError(f"{label} must contain only passed acceptance checks")
    return result


def verify_production_signoff(
    release_record_path: Path, rollback_record_path: Path, signoff_path: Path
) -> dict[str, Any]:
    release = read_record(release_record_path)
    rollback = read_record(rollback_record_path)
    signoff = read_json(signoff_path)

    if release.get("status") != "passed" or release.get("uat_mode") != "full":
        raise ValueError("release record must be passed with full UAT")
    candidate_commit = release.get("candidate_commit", "")
    if not COMMIT_PATTERN.fullmatch(candidate_commit):
        raise ValueError("release candidate_commit must be a full lowercase Git commit")
    api_image = release.get("api_image", "")
    web_image = release.get("web_image", "")
    if not IMAGE_PATTERN.fullmatch(api_image) or not IMAGE_PATTERN.fullmatch(web_image):
        raise ValueError("release images must use immutable sha256 digests")
    backup = release.get("backup", "")
    if not backup or backup == "pending":
        raise ValueError("release record must contain the completed backup artifact")

    if rollback.get("status") != "passed" or rollback.get("uat_mode") != "full":
        raise ValueError("rollback record must be passed with full UAT")
    if rollback.get("image_pull") != "immutable-digest":
        raise ValueError("rollback record must use immutable digest image pull mode")
    if rollback.get("candidate_commit") != candidate_commit:
        raise ValueError("rollback candidate commit does not match the release record")
    previous_commit = rollback.get("previous_commit", "")
    if not COMMIT_PATTERN.fullmatch(previous_commit) or previous_commit == candidate_commit:
        raise ValueError("rollback previous_commit must identify a different full Git commit")
    if rollback.get("candidate_api_image") != api_image or rollback.get("candidate_web_image") != web_image:
        raise ValueError("rollback candidate images do not match the release record")
    previous_images = (rollback.get("previous_api_image", ""), rollback.get("previous_web_image", ""))
    if not all(IMAGE_PATTERN.fullmatch(image) for image in previous_images):
        raise ValueError("rollback previous images must use immutable sha256 digests")
    if previous_images == (api_image, web_image):
        raise ValueError("rollback previous images must differ from the candidate images")
    if rollback.get("backup") != backup:
        raise ValueError("rollback must restore the backup captured by the release")

    release_uat_path = resolve_evidence_path(release_record_path, require_value(release, "uat_result", "release"))
    rollback_uat_path = resolve_evidence_path(
        rollback_record_path, require_value(rollback, "uat_result", "rollback")
    )
    validate_uat(release_uat_path, "release UAT")
    validate_uat(rollback_uat_path, "rollback UAT")

    expected_hashes = {
        "release_record_sha256": file_sha256(release_record_path),
        "release_uat_sha256": file_sha256(release_uat_path),
        "rollback_record_sha256": file_sha256(rollback_record_path),
        "rollback_uat_sha256": file_sha256(rollback_uat_path),
    }
    for key, expected in expected_hashes.items():
        if signoff.get(key) != expected:
            raise ValueError(f"signoff.{key} does not match the acceptance evidence")
    if signoff.get("candidate_commit") != candidate_commit:
        raise ValueError("signoff candidate_commit does not match the release record")
    for key in ("environment", "host_specification"):
        require_text(signoff, key, "signoff")
    validate_test_window(signoff)
    for key in ("security_scan_result", "load_test_result", "backup_restore_result"):
        validate_passed_gate(signoff, key)
    defects = require_value(signoff, "open_defects", "signoff")
    if not isinstance(defects, dict) or defects.get("blocking") != 0 or defects.get("high") != 0:
        raise ValueError("signoff.open_defects must report zero blocking and high defects")
    business = validate_approval(signoff, "business_signoff")
    operations = validate_approval(signoff, "operations_signoff")

    return {
        "status": "passed",
        "candidate_commit": candidate_commit,
        "previous_commit": previous_commit,
        "api_image": api_image,
        "web_image": web_image,
        "backup": backup,
        "evidence_sha256": expected_hashes,
        "business_signoff": business,
        "operations_signoff": operations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify final production acceptance and signoff evidence")
    parser.add_argument("--release-record", type=Path, required=True)
    parser.add_argument("--rollback-record", type=Path, required=True)
    parser.add_argument("--signoff", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        result = verify_production_signoff(args.release_record, args.rollback_record, args.signoff)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
