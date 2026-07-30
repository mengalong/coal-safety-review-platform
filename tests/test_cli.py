from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import verify_password
from coal_platform.cli import bootstrap_admin, reset_user_password
from coal_platform.database import Base
from coal_platform.models import AuthSession, OperationLog, User


def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def test_bootstrap_admin_creates_only_the_first_administrator() -> None:
    factory = session_factory()
    user_id = bootstrap_admin(factory, "initial-admin", "初始管理员", "strong-password-2026")
    with factory() as session:
        user = session.get(User, UUID(user_id))
        assert user is not None
        assert user.role == "admin"
        assert verify_password("strong-password-2026", user.password_hash)
    with pytest.raises(ValueError, match="already exists"):
        bootstrap_admin(factory, "another-admin", "另一管理员", "another-password-2026")


def test_bootstrap_admin_rejects_short_password() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        bootstrap_admin(session_factory(), "admin", "管理员", "too-short")


def test_reset_user_password_replaces_hash_revokes_sessions_and_writes_audit() -> None:
    factory = session_factory()
    user_id = bootstrap_admin(factory, "uat-admin", "UAT管理员", "original-password-2026")
    with factory() as session, session.begin():
        session.add_all(
            [
                AuthSession(
                    user_id=UUID(user_id),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    status="active",
                ),
                AuthSession(
                    user_id=UUID(user_id),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    status="revoked",
                    revoked_at=datetime.now(UTC),
                ),
            ]
        )

    reset_user_id, revoked_sessions = reset_user_password(factory, "uat-admin", "replacement-password-2026")

    assert reset_user_id == user_id
    assert revoked_sessions == 1
    with factory() as session:
        user = session.get(User, UUID(user_id))
        assert user is not None
        assert verify_password("replacement-password-2026", user.password_hash)
        assert not verify_password("original-password-2026", user.password_hash)
        sessions = list(session.scalars(select(AuthSession).where(AuthSession.user_id == UUID(user_id))))
        assert all(item.status == "revoked" for item in sessions)
        log = session.scalar(select(OperationLog).where(OperationLog.action_code == "user.password.reset"))
        assert log is not None
        assert log.operator_user_id is None
        assert log.after_snapshot == {"login_name": "uat-admin", "sessions_revoked": 1}
        assert "password" not in str(log.after_snapshot).lower()


@pytest.mark.parametrize(
    ("login_name", "password", "message"),
    [
        ("missing-user", "replacement-password-2026", "does not exist"),
        ("uat-admin", "too-short", "at least 12"),
    ],
)
def test_reset_user_password_rejects_missing_user_and_short_password(
    login_name: str, password: str, message: str
) -> None:
    factory = session_factory()
    bootstrap_admin(factory, "uat-admin", "UAT管理员", "original-password-2026")

    with pytest.raises(ValueError, match=message):
        reset_user_password(factory, login_name, password)
