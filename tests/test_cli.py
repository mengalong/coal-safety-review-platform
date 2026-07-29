from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import verify_password
from coal_platform.cli import bootstrap_admin
from coal_platform.database import Base
from coal_platform.models import User


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
