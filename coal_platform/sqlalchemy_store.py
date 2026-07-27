from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import hash_password, verify_password
from coal_platform.models import AuditIssue, AuditRound, AuditTask, AuthSession, OperationLog, TaskFile, User
from coal_platform.store import DemoStore


def _uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class SqlAlchemyStore(DemoStore):
    """Database-backed P0 resources with the demo catalog retained for P1 APIs."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__()
        self.session_factory = session_factory

    def initialize(self, seed_demo_data: bool = True) -> None:
        with self.session_factory() as session:
            session.scalar(select(User.id).limit(1))
        if seed_demo_data:
            self._seed_database()

    def healthcheck(self) -> bool:
        with self.session_factory() as session:
            return session.scalar(select(1)) == 1

    def _seed_database(self) -> None:
        with self.session_factory() as session, session.begin():
            if session.scalar(select(func.count()).select_from(User)):
                return

            reviewer = User(
                login_name="liming",
                password_hash=hash_password(self.demo_password),
                display_name="李明",
                role="reviewer",
                status="active",
            )
            admin = User(
                login_name="admin",
                password_hash=hash_password(self.demo_password),
                display_name="陈静",
                role="admin",
                status="active",
            )
            session.add_all([reviewer, admin])
            session.flush()

            demo_tasks = [
                (
                    "SH-2026-000128",
                    "晋北装备制造有限公司",
                    "带式输送机",
                    "DSJ80/40/2×75",
                    reviewer.id,
                    "waiting_review",
                    2,
                    "客户补充整改文件",
                ),
                (
                    "SH-2026-000127",
                    "华煤机械科技有限公司",
                    "矿用隔爆型真空馈电开关",
                    "KBZ-400/1140",
                    reviewer.id,
                    "auditing",
                    1,
                    "文件解析中",
                ),
                (
                    "SH-2026-000124",
                    "山西北辰机电有限公司",
                    "矿用本安型显示器",
                    "XH12",
                    admin.id,
                    "waiting_standards",
                    1,
                    "待确认标准",
                ),
            ]
            for task_data in demo_tasks:
                task = AuditTask(
                    task_no=task_data[0],
                    customer_name=task_data[1],
                    product_name=task_data[2],
                    product_model=task_data[3],
                    owner_user_id=task_data[4],
                    status=task_data[5],
                    current_round_no=task_data[6],
                )
                session.add(task)
                session.flush()
                round_item = AuditRound(
                    task_id=task.id,
                    round_no=task_data[6],
                    status=task_data[5],
                    round_note=task_data[7],
                    basic_info_snapshot={
                        "customer_name": task.customer_name,
                        "product_name": task.product_name,
                        "product_model": task.product_model,
                    },
                )
                session.add(round_item)
                session.flush()
                task.current_round_id = round_item.id

    @staticmethod
    def _user_dict(user: User) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "login_name": user.login_name,
            "display_name": user.display_name,
            "role": user.role,
            "status": user.status,
            "phone": user.phone,
            "email": user.email,
            "last_login_at": _iso(user.last_login_at),
            "created_at": _iso(user.created_at),
            "updated_at": _iso(user.updated_at),
        }

    @staticmethod
    def _round_dict(round_item: AuditRound) -> dict[str, Any]:
        return {
            "id": str(round_item.id),
            "task_id": str(round_item.task_id),
            "round_no": round_item.round_no,
            "status": round_item.status,
            "suggested_conclusion": round_item.suggested_conclusion,
            "manual_conclusion": round_item.manual_conclusion,
            "round_note": round_item.round_note,
            "basic_info_snapshot": round_item.basic_info_snapshot,
            "standards": [],
            "created_at": _iso(round_item.created_at),
            "updated_at": _iso(round_item.updated_at),
        }

    @staticmethod
    def _file_dict(file_item: TaskFile) -> dict[str, Any]:
        return {
            "id": str(file_item.id),
            "task_id": str(file_item.task_id),
            "round_id": str(file_item.round_id) if file_item.round_id else None,
            "file_name": file_item.original_name,
            "file_type": file_item.file_type,
            "content_type": file_item.mime_type,
            "file_size": file_item.file_size,
            "sha256": file_item.sha256,
            "storage_key": file_item.storage_key,
            "version_no": file_item.version_no,
            "status": file_item.status,
            "created_at": _iso(file_item.created_at),
            "updated_at": _iso(file_item.updated_at),
        }

    def _task_dict(self, session: Session, task: AuditTask) -> dict[str, Any]:
        owner = session.get(User, task.owner_user_id) if task.owner_user_id else None
        rounds = list(
            session.scalars(select(AuditRound).where(AuditRound.task_id == task.id).order_by(AuditRound.round_no))
        )
        files = list(session.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.created_at)))
        round_ids = [item.id for item in rounds]
        issue_ids: list[str] = []
        if round_ids:
            issue_ids = [
                str(issue_id)
                for issue_id in session.scalars(select(AuditIssue.id).where(AuditIssue.round_id.in_(round_ids)))
            ]
        current_round = next((item for item in rounds if item.id == task.current_round_id), None)
        return {
            "id": str(task.id),
            "task_no": task.task_no,
            "customer_name": task.customer_name,
            "product_name": task.product_name,
            "product_model": task.product_model,
            "owner_user_id": str(task.owner_user_id) if task.owner_user_id else None,
            "owner_user_name": owner.display_name if owner else None,
            "status": task.status,
            "current_round_no": task.current_round_no,
            "current_round_id": str(task.current_round_id) if task.current_round_id else None,
            "final_conclusion": task.final_conclusion,
            "round_note": current_round.round_note if current_round else None,
            "files": [self._file_dict(item) for item in files],
            "rounds": [self._round_dict(item) for item in rounds],
            "issues": issue_ids,
            "created_at": _iso(task.created_at),
            "updated_at": _iso(task.updated_at),
        }

    @staticmethod
    def _log(
        session: Session,
        *,
        operator_user_id: str | UUID | None,
        entity_type: str,
        entity_id: UUID,
        action_code: str,
        before_snapshot: dict[str, Any] | None = None,
        after_snapshot: dict[str, Any] | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        session.add(
            OperationLog(
                operator_user_id=_uuid(operator_user_id),
                entity_type=entity_type,
                entity_id=entity_id,
                action_code=action_code,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                reason=reason,
                trace_id=trace_id,
            )
        )

    def authenticate(self, login_name: str, password: str) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            user = session.scalar(select(User).where(User.login_name == login_name))
            if not user or user.status != "active" or not verify_password(password, user.password_hash):
                return None
            user.last_login_at = datetime.now(UTC)
            session.flush()
            return self._user_dict(user)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user_uuid = _uuid(user_id)
        if not user_uuid:
            return None
        with self.session_factory() as session:
            user = session.get(User, user_uuid)
            return self._user_dict(user) if user else None

    def create_auth_session(self, user_id: str, expires_at: datetime) -> str:
        user_uuid = _uuid(user_id)
        if not user_uuid:
            raise ValueError("invalid user id")
        with self.session_factory() as session, session.begin():
            auth_session = AuthSession(user_id=user_uuid, expires_at=expires_at, status="active")
            session.add(auth_session)
            session.flush()
            return str(auth_session.id)

    def is_auth_session_active(self, session_id: str, user_id: str) -> bool:
        session_uuid = _uuid(session_id)
        user_uuid = _uuid(user_id)
        if not session_uuid or not user_uuid:
            return False
        with self.session_factory() as session:
            auth_session = session.get(AuthSession, session_uuid)
            if not auth_session or auth_session.user_id != user_uuid:
                return False
            expires_at = auth_session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            return auth_session.status == "active" and auth_session.revoked_at is None and expires_at > datetime.now(UTC)

    def revoke_auth_session(self, session_id: str, user_id: str) -> bool:
        session_uuid = _uuid(session_id)
        user_uuid = _uuid(user_id)
        if not session_uuid or not user_uuid:
            return False
        with self.session_factory() as session, session.begin():
            auth_session = session.get(AuthSession, session_uuid)
            if not auth_session or auth_session.user_id != user_uuid or auth_session.status != "active":
                return False
            auth_session.status = "revoked"
            auth_session.revoked_at = datetime.now(UTC)
            return True

    def current_user(self, user_id: str | None = None) -> dict[str, Any]:
        with self.session_factory() as session:
            query: Select[tuple[User]] = select(User)
            if user_id and (user_uuid := _uuid(user_id)):
                query = query.where(User.id == user_uuid)
            else:
                query = query.where(User.role == "reviewer").order_by(User.created_at)
            user = session.scalar(query.limit(1))
            if not user:
                raise LookupError("no active platform user")
            return self._user_dict(user)

    def list_users(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            users = session.scalars(select(User).order_by(User.created_at)).all()
            return [self._user_dict(user) for user in users]

    def list_tasks(self, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            query = select(AuditTask).order_by(AuditTask.created_at.desc())
            if owner_user_id and (owner_uuid := _uuid(owner_user_id)):
                query = query.where(AuditTask.owner_user_id == owner_uuid)
            return [self._task_dict(session, task) for task in session.scalars(query)]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task_uuid = _uuid(task_id)
        if not task_uuid:
            return None
        with self.session_factory() as session:
            task = session.get(AuditTask, task_uuid)
            return self._task_dict(session, task) if task else None

    def _next_task_no(self, session: Session) -> str:
        year = datetime.now(UTC).year
        latest = session.scalar(
            select(AuditTask.task_no)
            .where(AuditTask.task_no.like(f"SH-{year}-%"))
            .order_by(AuditTask.task_no.desc())
            .limit(1)
        )
        sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
        return f"SH-{year}-{sequence:06d}"

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            owner_uuid = _uuid(payload.get("owner_user_id"))
            if not owner_uuid:
                owner_uuid = session.scalar(select(User.id).where(User.role == "reviewer").limit(1))
            task = AuditTask(
                task_no=self._next_task_no(session),
                customer_name=payload.get("customer_name") or "待确认客户",
                product_name=payload.get("product_name") or "待确认产品",
                product_model=payload.get("product_model") or "待确认型号",
                owner_user_id=owner_uuid,
                status="draft",
                current_round_no=1,
            )
            session.add(task)
            session.flush()
            round_item = AuditRound(
                task_id=task.id,
                round_no=1,
                status="draft",
                round_note=payload.get("round_note") or "",
                basic_info_snapshot={
                    "customer_name": task.customer_name,
                    "product_name": task.product_name,
                    "product_model": task.product_model,
                },
            )
            session.add(round_item)
            session.flush()
            task.current_round_id = round_item.id
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_task",
                entity_id=task.id,
                action_code="task.create",
                after_snapshot={"task_no": task.task_no, "product_model": task.product_model},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._task_dict(session, task)

    def update_task_basic_info(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task_uuid = _uuid(task_id)
        if not task_uuid:
            return None
        with self.session_factory() as session, session.begin():
            task = session.get(AuditTask, task_uuid)
            if not task:
                return None
            before = {
                "customer_name": task.customer_name,
                "product_name": task.product_name,
                "product_model": task.product_model,
            }
            for key in ("customer_name", "product_name", "product_model"):
                if payload.get(key):
                    setattr(task, key, payload[key])
            after = {
                "customer_name": task.customer_name,
                "product_name": task.product_name,
                "product_model": task.product_model,
            }
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_task",
                entity_id=task.id,
                action_code="task.basic_info.update",
                before_snapshot=before,
                after_snapshot=after,
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._task_dict(session, task)

    def add_task_files(self, task_id: str, files: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        task_uuid = _uuid(task_id)
        if not task_uuid:
            return None
        with self.session_factory() as session, session.begin():
            task = session.get(AuditTask, task_uuid)
            if not task:
                return None
            created: list[dict[str, Any]] = []
            for item in files:
                existing = session.scalar(
                    select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.sha256 == item["sha256"])
                )
                if existing:
                    created.append(self._file_dict(existing))
                    continue
                file_item = TaskFile(
                    task_id=task.id,
                    round_id=task.current_round_id,
                    storage_key=item["storage_key"],
                    original_name=item["file_name"],
                    file_type=item.get("file_type") or "other",
                    mime_type=item.get("content_type"),
                    file_size=item["file_size"],
                    sha256=item["sha256"],
                    version_no=1,
                    status="uploaded",
                )
                session.add(file_item)
                session.flush()
                self._log(
                    session,
                    operator_user_id=item.get("_operator_user_id"),
                    entity_type="task_file",
                    entity_id=file_item.id,
                    action_code="task_file.upload",
                    after_snapshot={"file_name": file_item.original_name, "sha256": file_item.sha256},
                    trace_id=item.get("_trace_id"),
                )
                created.append(self._file_dict(file_item))
            return created

    def create_round(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task_uuid = _uuid(task_id)
        if not task_uuid:
            return None
        with self.session_factory() as session, session.begin():
            task = session.get(AuditTask, task_uuid)
            if not task:
                return None
            round_item = AuditRound(
                task_id=task.id,
                round_no=task.current_round_no + 1,
                status="draft",
                round_note=payload.get("round_note") or "",
                basic_info_snapshot={
                    "customer_name": task.customer_name,
                    "product_name": task.product_name,
                    "product_model": task.product_model,
                },
            )
            session.add(round_item)
            session.flush()
            task.current_round_no = round_item.round_no
            task.current_round_id = round_item.id
            task.status = "in_new_round"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_round",
                entity_id=round_item.id,
                action_code="audit_round.create",
                after_snapshot={"round_no": round_item.round_no},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._round_dict(round_item)

    def get_round(self, round_id: str) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            round_item = session.get(AuditRound, round_uuid)
            return self._round_dict(round_item) if round_item else None

    def add_standard_to_round(self, round_id: str, payload: dict) -> dict | None:
        round_item = self.get_round(round_id)
        if not round_item:
            return None
        return {
            "id": str(uuid4()),
            "round_id": round_id,
            "standard_version_id": payload.get("standard_version_id"),
            "standard_code": payload.get("standard_code"),
            "standard_name": payload.get("standard_name"),
            "source_type": payload.get("source_type", "document_reference"),
            "status": "selected",
            "skip_reason": None,
        }

    def list_operation_logs(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            logs = session.scalars(select(OperationLog).order_by(OperationLog.created_at.desc())).all()
            return [
                {
                    "id": str(item.id),
                    "operator_user_id": str(item.operator_user_id) if item.operator_user_id else None,
                    "entity_type": item.entity_type,
                    "entity_id": str(item.entity_id),
                    "action_code": item.action_code,
                    "before_snapshot": item.before_snapshot,
                    "after_snapshot": item.after_snapshot,
                    "reason": item.reason,
                    "trace_id": item.trace_id,
                    "created_at": _iso(item.created_at),
                }
                for item in logs
            ]
