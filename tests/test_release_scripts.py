import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_release_uat(tmp_path: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "COAL_PUBLIC_ORIGIN=https://coal.example.test\nCOAL_TLS_CERT_FILE=/missing/ca.pem\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["sh", "scripts/release-uat.sh", str(env_file), str(tmp_path / "uat.json")],
        cwd=ROOT,
        env={**os.environ, **environment},
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_uat_requires_runtime_password(tmp_path: Path) -> None:
    result = run_release_uat(tmp_path, COAL_UAT_PASSWORD="")

    assert result.returncode == 1
    assert "COAL_UAT_PASSWORD is required" in result.stderr


def test_full_release_uat_requires_explicit_model_cost_confirmation(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test-ca", encoding="utf-8")
    result = run_release_uat(
        tmp_path,
        COAL_UAT_PASSWORD="test-password",
        COAL_UAT_MODE="full",
        COAL_UAT_CA_FILE=str(ca_file),
        COAL_UAT_CONFIRM_MODEL_COST="false",
    )

    assert result.returncode == 1
    assert "COAL_UAT_CONFIRM_MODEL_COST=true is required" in result.stderr


def test_release_uat_rejects_unknown_mode(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test-ca", encoding="utf-8")
    result = run_release_uat(
        tmp_path,
        COAL_UAT_PASSWORD="test-password",
        COAL_UAT_MODE="unsupported",
        COAL_UAT_CA_FILE=str(ca_file),
    )

    assert result.returncode == 1
    assert "COAL_UAT_MODE must be basic or full" in result.stderr


def test_rollback_rejects_unknown_drill_mode(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "sh",
            "scripts/rollback.sh",
            "--confirm",
            str(tmp_path / "previous.env"),
            str(tmp_path / "backup"),
            str(tmp_path / "current.env"),
            "--unsafe-skip-pull",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "fifth argument must be --local-drill" in result.stderr
