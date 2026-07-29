from __future__ import annotations

import argparse
import getpass
import os
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import hash_password
from coal_platform.database import SessionLocal
from coal_platform.models import User


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m coal_platform.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-admin", help="create the first production administrator")
    bootstrap.add_argument("--login-name", default="admin")
    bootstrap.add_argument("--display-name", required=True)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
