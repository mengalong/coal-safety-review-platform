from __future__ import annotations

import argparse
import getpass
import os
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import hash_password
from coal_platform.database import SessionLocal
from coal_platform.models import AuthSession, OperationLog, User


def bootstrap_admin(
    session_factory: sessionmaker[Session],
    login_name: str,
    display_name: str,
    password: str,
) -> str:
    if len(password) < 12:
        raise ValueError("bootstrap administrator password must contain at least 12 characters")
    with session_factory() as session, session.begin():
        if session.scalar(select(User.id).where(User.role == "admin")):
            raise ValueError("an administrator already exists; bootstrap is disabled")
        if session.scalar(select(User.id).where(User.login_name == login_name)):
            raise ValueError("login name already exists")
        user = User(
            login_name=login_name,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
            status="active",
        )
        session.add(user)
        session.flush()
        return str(user.id)


def reset_user_password(
    session_factory: sessionmaker[Session], login_name: str, password: str
) -> tuple[str, int]:
    if len(password) < 12:
        raise ValueError("production password must contain at least 12 characters")
    with session_factory() as session, session.begin():
        user = session.scalar(select(User).where(User.login_name == login_name).with_for_update())
        if not user:
            raise ValueError("user does not exist")
        if user.status != "active":
            raise ValueError("only an active user password can be reset")
        user.password_hash = hash_password(password)
        revoked_at = datetime.now(UTC)
        active_sessions = list(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user.id,
                    AuthSession.status == "active",
                )
            )
        )
        for auth_session in active_sessions:
            auth_session.status = "revoked"
            auth_session.revoked_at = revoked_at
        session.add(
            OperationLog(
                operator_user_id=None,
                entity_type="user",
                entity_id=user.id,
                action_code="user.password.reset",
                after_snapshot={"login_name": user.login_name, "sessions_revoked": len(active_sessions)},
                reason="production break-glass CLI",
                trace_id="cli-password-reset",
            )
        )
        return str(user.id), len(active_sessions)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m coal_platform.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-admin", help="create the first production administrator")
    bootstrap.add_argument("--login-name", default="admin")
    bootstrap.add_argument("--display-name", required=True)
    reset = subparsers.add_parser("reset-password", help="reset an existing active production user password")
    reset.add_argument("--login-name", required=True)
    reset.add_argument("--confirm", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "bootstrap-admin":
        password = os.getenv("COAL_BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Administrator password: ")
        try:
            user_id = bootstrap_admin(SessionLocal, args.login_name, args.display_name, password)
        except ValueError as exc:
            print(f"Bootstrap refused: {exc}")
            return 2
        print(f"Administrator created: {args.login_name} ({user_id})")
    elif args.command == "reset-password":
        if not args.confirm:
            print("Password reset refused: --confirm is required")
            return 2
        password = os.getenv("COAL_RESET_USER_PASSWORD") or getpass.getpass("New password: ")
        if "COAL_RESET_USER_PASSWORD" not in os.environ:
            confirmation = getpass.getpass("Confirm new password: ")
            if password != confirmation:
                print("Password reset refused: passwords do not match")
                return 2
        try:
            user_id, revoked_sessions = reset_user_password(SessionLocal, args.login_name, password)
        except ValueError as exc:
            print(f"Password reset refused: {exc}")
            return 2
        print(f"Password reset completed: {args.login_name} ({user_id}); revoked sessions: {revoked_sessions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
