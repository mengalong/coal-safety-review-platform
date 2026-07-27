from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import hash_password, verify_password
from coal_platform.models import (
    AuditIssue,
    AuditRound,
    AuditRun,
    AuditTask,
    AuthSession,
    OperationLog,
    QueueJob,
    RoundStandard,
    Standard,
    StandardClause,
    StandardParseRevision,
    StandardVersion,
    TaskFile,
    User,
)
from coal_platform.store import DemoStore, compare_clause_sets


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
                self._seed_standard_catalog(session)
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
            self._seed_standard_catalog(session)

    @staticmethod
    def _seed_standard_catalog(session: Session) -> None:
        if session.scalar(select(Standard.id).limit(1)):
            return
        catalog = [
            ("GB/T 10595", "带式输送机", "国家标准", "2017", "GB/T 10595-2017"),
            ("MT/T 820", "煤矿用带式输送机 技术条件", "行业标准", "2023", "MT/T 820-2023"),
            ("MT 820", "煤矿用带式输送机 技术条件", "行业标准", "2006", "MT 820-2006"),
            ("GB/T 191", "包装储运图示标志", "国家标准", "2008", "GB/T 191-2008"),
            ("MT/T 154.1", "煤矿机电产品型号编制方法", "行业标准", "2011", "MT/T 154.1-2011"),
        ]
        for code, name, standard_type, version_label, full_code in catalog:
            standard = Standard(
                standard_code=code,
                standard_name=name,
                standard_type=standard_type,
                status="obsolete" if version_label == "2006" else "active",
            )
            session.add(standard)
            session.flush()
            version = StandardVersion(
                standard_id=standard.id,
                full_code=full_code,
                version_label=version_label,
                publish_date=date(int(version_label), 12, 20),
                implement_date=date(int(version_label) + 1, 7, 1),
                publisher="国家标准化管理委员会" if standard_type == "国家标准" else "国家矿山安全监察局",
                mandatory_flag=False,
                status="obsolete" if version_label == "2006" else "active",
            )
            session.add(version)
            session.flush()
            revision = StandardParseRevision(
                standard_version_id=version.id,
                revision_no="P1",
                revision_payload={"source": "seed", "clause_count": 2},
                impact_flag="no_impact",
                status="published",
                published_at=datetime.now(UTC),
            )
            session.add(revision)
            session.flush()
            clauses = [
                ("5.3.2", "驱动功率配置", "必须", "驱动装置的配置及额定功率应满足设计输送能力，并与产品型号标示一致。"),
                ("附录A", "受控件类别", "待确认", "受控件类别应依据现行实施规则确认。"),
            ]
            for clause_code, title, constraint_level, original_text in clauses:
                session.add(
                    StandardClause(
                        parse_revision_id=revision.id,
                        clause_code=clause_code,
                        title=title,
                        clause_level=2,
                        clause_type="requirement",
                        constraint_level=constraint_level,
                        original_text=original_text,
                        parameter_schema={},
                        confidence=0.98,
                        proof_status="confirmed",
                    )
                )

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

    def _round_dict(self, round_item: AuditRound, session: Session | None = None) -> dict[str, Any]:
        standards = []
        if session:
            standards = [
                self._round_standard_dict(session, item)
                for item in session.scalars(
                    select(RoundStandard).where(RoundStandard.round_id == round_item.id).order_by(RoundStandard.created_at)
                )
            ]
        return {
            "id": str(round_item.id),
            "task_id": str(round_item.task_id),
            "round_no": round_item.round_no,
            "status": round_item.status,
            "suggested_conclusion": round_item.suggested_conclusion,
            "manual_conclusion": round_item.manual_conclusion,
            "round_note": round_item.round_note,
            "basic_info_snapshot": round_item.basic_info_snapshot,
            "standards": standards,
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

    @staticmethod
    def _clause_dict(clause: StandardClause) -> dict[str, Any]:
        return {
            "id": str(clause.id),
            "parse_revision_id": str(clause.parse_revision_id),
            "clause_code": clause.clause_code,
            "title": clause.title,
            "clause_level": clause.clause_level,
            "clause_type": clause.clause_type,
            "constraint_level": clause.constraint_level,
            "original_text": clause.original_text,
            "parameter_schema": clause.parameter_schema,
            "page_no": clause.page_no,
            "confidence": float(clause.confidence),
            "proof_status": clause.proof_status,
        }

    def _parse_revision_dict(self, session: Session, revision: StandardParseRevision | None, include_clauses: bool = False) -> dict[str, Any] | None:
        if not revision:
            return None
        item = {
            "id": str(revision.id),
            "standard_version_id": str(revision.standard_version_id),
            "revision_no": revision.revision_no,
            "revision_payload": revision.revision_payload,
            "impact_flag": revision.impact_flag,
            "status": revision.status,
            "published_at": _iso(revision.published_at),
            "clauses": [],
        }
        if include_clauses:
            item["clauses"] = [
                self._clause_dict(clause)
                for clause in session.scalars(
                    select(StandardClause).where(StandardClause.parse_revision_id == revision.id).order_by(StandardClause.clause_code)
                )
            ]
        return item

    def _standard_version_dict(self, session: Session, version: StandardVersion, include_clauses: bool = False) -> dict[str, Any]:
        standard = session.get(Standard, version.standard_id)
        revision = session.scalar(
            select(StandardParseRevision)
            .where(StandardParseRevision.standard_version_id == version.id)
            .order_by(StandardParseRevision.created_at.desc(), StandardParseRevision.revision_no.desc())
            .limit(1)
        )
        return {
            "id": str(version.id),
            "standard_id": str(version.standard_id),
            "standard_code": standard.standard_code if standard else None,
            "standard_name": standard.standard_name if standard else None,
            "full_code": version.full_code,
            "version_label": version.version_label,
            "publish_date": version.publish_date.isoformat() if version.publish_date else None,
            "implement_date": version.implement_date.isoformat() if version.implement_date else None,
            "abolish_date": version.abolish_date.isoformat() if version.abolish_date else None,
            "publisher": version.publisher,
            "mandatory_flag": version.mandatory_flag,
            "status": version.status,
            "superseded_by_id": str(version.superseded_by_id) if version.superseded_by_id else None,
            "latest_parse_revision": self._parse_revision_dict(session, revision, include_clauses),
        }

    def _standard_dict(self, session: Session, standard: Standard, include_clauses: bool = False) -> dict[str, Any]:
        versions = list(
            session.scalars(select(StandardVersion).where(StandardVersion.standard_id == standard.id).order_by(StandardVersion.version_label.desc()))
        )
        return {
            "id": str(standard.id),
            "standard_code": standard.standard_code,
            "standard_name": standard.standard_name,
            "standard_type": standard.standard_type,
            "scope_text": standard.scope_text,
            "keywords": standard.keywords or [],
            "alias_texts": standard.alias_texts or [],
            "status": standard.status,
            "versions": [self._standard_version_dict(session, version, include_clauses) for version in versions],
        }

    def _round_standard_dict(self, session: Session, item: RoundStandard) -> dict[str, Any]:
        version = session.get(StandardVersion, item.standard_version_id)
        standard = session.get(Standard, version.standard_id) if version else None
        return {
            "id": str(item.id),
            "round_id": str(item.round_id),
            "standard_version_id": str(item.standard_version_id),
            "parse_revision_id": str(item.parse_revision_id),
            "standard_code": version.full_code if version else None,
            "standard_name": standard.standard_name if standard else None,
            "source_type": item.source_type,
            "snapshot_no": item.snapshot_no,
            "status": "confirmed" if item.snapshot_no.startswith("SNAPSHOT-") else "selected",
            "skip_reason": item.skip_reason,
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
            "rounds": [self._round_dict(item, session) for item in rounds],
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

    def list_standards(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            standards = session.scalars(select(Standard).order_by(Standard.standard_code)).all()
            return [self._standard_dict(session, item) for item in standards]

    def get_standard(self, standard_id: str) -> dict[str, Any] | None:
        standard_uuid = _uuid(standard_id)
        if not standard_uuid:
            return None
        with self.session_factory() as session:
            standard = session.get(Standard, standard_uuid)
            return self._standard_dict(session, standard, include_clauses=True) if standard else None

    def create_standard(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            if session.scalar(select(Standard.id).where(Standard.standard_code == payload["standard_code"])):
                return None
            standard = Standard(
                standard_code=payload["standard_code"],
                standard_name=payload["standard_name"],
                standard_type=payload["standard_type"],
                scope_text=payload.get("scope_text"),
                keywords=payload.get("keywords") or [],
                alias_texts=payload.get("alias_texts") or [],
                status="draft",
            )
            session.add(standard)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard",
                entity_id=standard.id,
                action_code="standard.create",
                after_snapshot={"standard_code": standard.standard_code},
                trace_id=payload.get("_trace_id"),
            )
            return self._standard_dict(session, standard)

    def list_standard_versions(self, standard_id: str) -> list[dict[str, Any]]:
        standard_uuid = _uuid(standard_id)
        if not standard_uuid:
            return []
        with self.session_factory() as session:
            versions = session.scalars(
                select(StandardVersion)
                .where(StandardVersion.standard_id == standard_uuid)
                .order_by(StandardVersion.version_label.desc())
            ).all()
            return [self._standard_version_dict(session, item) for item in versions]

    def get_standard_version(self, version_id: str) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            version = session.get(StandardVersion, version_uuid)
            return self._standard_version_dict(session, version, include_clauses=True) if version else None

    def create_standard_version(self, standard_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        standard_uuid = _uuid(standard_id)
        if not standard_uuid:
            return None
        with self.session_factory() as session, session.begin():
            standard = session.get(Standard, standard_uuid)
            if not standard:
                return None
            full_code = payload.get("full_code") or f"{standard.standard_code}-{payload['version_label']}"
            if session.scalar(select(StandardVersion.id).where(StandardVersion.full_code == full_code)):
                return None
            version = StandardVersion(
                standard_id=standard.id,
                full_code=full_code,
                version_label=payload["version_label"],
                publish_date=payload.get("publish_date"),
                implement_date=payload.get("implement_date"),
                abolish_date=payload.get("abolish_date"),
                publisher=payload.get("publisher"),
                mandatory_flag=payload.get("mandatory_flag", False),
                status=payload.get("status", "draft"),
            )
            session.add(version)
            session.flush()
            revision = StandardParseRevision(
                standard_version_id=version.id,
                revision_no="P1",
                revision_payload={"source": "manual", "clause_count": 0},
                impact_flag="no_impact",
                status="draft",
            )
            session.add(revision)
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard_version",
                entity_id=version.id,
                action_code="standard_version.create",
                after_snapshot={"full_code": version.full_code},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._standard_version_dict(session, version)

    def list_standard_parse_revisions(self, version_id: str) -> list[dict[str, Any]] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(StandardVersion, version_uuid):
                return None
            revisions = session.scalars(
                select(StandardParseRevision)
                .where(StandardParseRevision.standard_version_id == version_uuid)
                .order_by(StandardParseRevision.revision_no)
            ).all()
            return [self._parse_revision_dict(session, revision, include_clauses=True) for revision in revisions]

    def create_standard_parse_revision(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(StandardVersion, version_uuid)
            if not version:
                return None
            revisions = session.scalars(
                select(StandardParseRevision)
                .where(StandardParseRevision.standard_version_id == version.id)
                .order_by(StandardParseRevision.created_at, StandardParseRevision.revision_no)
            ).all()
            revision_numbers = [
                int(item.revision_no[1:])
                for item in revisions
                if item.revision_no.startswith("P") and item.revision_no[1:].isdigit()
            ]
            source_revision = revisions[-1] if revisions else None
            clause_payloads = payload.get("clauses")
            if clause_payloads is None and source_revision:
                source_clauses = session.scalars(
                    select(StandardClause)
                    .where(StandardClause.parse_revision_id == source_revision.id)
                    .order_by(StandardClause.clause_code)
                ).all()
                clause_payloads = [self._clause_dict(item) for item in source_clauses]
            clause_payloads = clause_payloads or []
            revision = StandardParseRevision(
                standard_version_id=version.id,
                revision_no=f"P{max(revision_numbers, default=0) + 1}",
                revision_payload={
                    "source": "manual",
                    "source_revision_id": str(source_revision.id) if source_revision else None,
                    "clause_count": len(clause_payloads),
                },
                impact_flag=payload.get("impact_flag", "no_impact"),
                status="draft",
            )
            session.add(revision)
            session.flush()
            for item in clause_payloads:
                session.add(
                    StandardClause(
                        parse_revision_id=revision.id,
                        clause_code=item["clause_code"],
                        title=item.get("title"),
                        clause_level=item.get("clause_level", 1),
                        clause_type=item.get("clause_type", "requirement"),
                        constraint_level=item.get("constraint_level", "待确认"),
                        original_text=item.get("original_text", ""),
                        parameter_schema=item.get("parameter_schema") or {},
                        page_no=item.get("page_no"),
                        bbox=item.get("bbox"),
                        confidence=item.get("confidence", 0.0),
                        proof_status=item.get("proof_status", "pending"),
                    )
                )
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard_parse_revision",
                entity_id=revision.id,
                action_code="standard_parse_revision.create",
                after_snapshot={"revision_no": revision.revision_no, "impact_flag": revision.impact_flag},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._parse_revision_dict(session, revision, include_clauses=True)

    def publish_standard_parse_revision(self, revision_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        revision_uuid = _uuid(revision_id)
        if not revision_uuid:
            return None
        with self.session_factory() as session, session.begin():
            revision = session.get(StandardParseRevision, revision_uuid)
            if not revision:
                return None
            for published in session.scalars(
                select(StandardParseRevision).where(
                    StandardParseRevision.standard_version_id == revision.standard_version_id,
                    StandardParseRevision.status == "published",
                    StandardParseRevision.id != revision.id,
                )
            ):
                published.status = "archived"
            revision.status = "published"
            revision.published_at = datetime.now(UTC)
            version = session.get(StandardVersion, revision.standard_version_id)
            if version:
                version.status = "active"
                standard = session.get(Standard, version.standard_id)
                if standard:
                    standard.status = "active"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard_parse_revision",
                entity_id=revision.id,
                action_code="standard_parse_revision.publish",
                after_snapshot={"status": "published"},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._parse_revision_dict(session, revision, include_clauses=True)

    def publish_standard_version(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(StandardVersion, version_uuid)
            if not version:
                return None
            version.status = "active"
            standard = session.get(Standard, version.standard_id)
            if standard:
                standard.status = "active"
            revision = session.scalar(
                select(StandardParseRevision)
                .where(StandardParseRevision.standard_version_id == version.id)
                .order_by(StandardParseRevision.created_at.desc(), StandardParseRevision.revision_no.desc())
                .limit(1)
            )
            if revision:
                for published in session.scalars(
                    select(StandardParseRevision).where(
                        StandardParseRevision.standard_version_id == version.id,
                        StandardParseRevision.status == "published",
                        StandardParseRevision.id != revision.id,
                    )
                ):
                    published.status = "archived"
                revision.status = "published"
                revision.published_at = datetime.now(UTC)
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard_version",
                entity_id=version.id,
                action_code="standard_version.publish",
                after_snapshot={"status": "active"},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._standard_version_dict(session, version)

    def abolish_standard_version(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(StandardVersion, version_uuid)
            if not version:
                return None
            successor_uuid = _uuid(payload.get("superseded_by_version_id"))
            successor = session.get(StandardVersion, successor_uuid) if successor_uuid else None
            if successor_uuid and (not successor or successor.standard_id != version.standard_id):
                return None
            before_status = version.status
            version.status = "obsolete"
            version.abolish_date = payload.get("abolish_date") or datetime.now(UTC).date()
            version.superseded_by_id = successor.id if successor else None
            standard = session.get(Standard, version.standard_id)
            if standard:
                active_version = session.scalar(
                    select(StandardVersion.id).where(
                        StandardVersion.standard_id == standard.id,
                        StandardVersion.status == "active",
                        StandardVersion.id != version.id,
                    )
                )
                standard.status = "active" if active_version else "obsolete"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="standard_version",
                entity_id=version.id,
                action_code="standard_version.abolish",
                before_snapshot={"status": before_status},
                after_snapshot={
                    "status": version.status,
                    "abolish_date": version.abolish_date.isoformat(),
                    "superseded_by_id": str(version.superseded_by_id) if version.superseded_by_id else None,
                },
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._standard_version_dict(session, version)

    def compare_standard_versions(self, version_id: str, other_version_id: str) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        other_uuid = _uuid(other_version_id)
        if not version_uuid or not other_uuid:
            return None
        with self.session_factory() as session:
            left = session.get(StandardVersion, version_uuid)
            right = session.get(StandardVersion, other_uuid)
            if not left or not right:
                return None
            revisions = []
            for version in (left, right):
                revision = session.scalar(
                    select(StandardParseRevision)
                    .where(StandardParseRevision.standard_version_id == version.id)
                    .order_by(StandardParseRevision.created_at.desc(), StandardParseRevision.revision_no.desc())
                    .limit(1)
                )
                revisions.append(revision)
            clause_sets = []
            for revision in revisions:
                clause_sets.append(
                    [
                        self._clause_dict(item)
                        for item in session.scalars(
                            select(StandardClause)
                            .where(StandardClause.parse_revision_id == revision.id)
                            .order_by(StandardClause.clause_code)
                        )
                    ]
                    if revision
                    else []
                )
            return {
                "left_version": {"id": str(left.id), "full_code": left.full_code},
                "right_version": {"id": str(right.id), "full_code": right.full_code},
                **compare_clause_sets(clause_sets[0], clause_sets[1]),
            }

    def list_standard_clauses(self, version_id: str) -> list[dict[str, Any]] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(StandardVersion, version_uuid):
                return None
            revision = session.scalar(
                select(StandardParseRevision)
                .where(StandardParseRevision.standard_version_id == version_uuid)
                .order_by(StandardParseRevision.created_at.desc(), StandardParseRevision.revision_no.desc())
                .limit(1)
            )
            if not revision:
                return []
            return [
                self._clause_dict(item)
                for item in session.scalars(
                    select(StandardClause).where(StandardClause.parse_revision_id == revision.id).order_by(StandardClause.clause_code)
                )
            ]

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
            return self._round_dict(round_item, session)

    def get_round(self, round_id: str) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            round_item = session.get(AuditRound, round_uuid)
            return self._round_dict(round_item, session) if round_item else None

    def start_audit(self, round_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session, session.begin():
            round_item = session.get(AuditRound, round_uuid)
            if not round_item:
                return None
            task = session.get(AuditTask, round_item.task_id)
            if not task:
                return None

            active_run = session.scalar(
                select(AuditRun)
                .where(AuditRun.round_id == round_item.id, AuditRun.status.in_(["queued", "running"]))
                .order_by(AuditRun.run_no.desc())
                .limit(1)
            )
            if active_run:
                return self._audit_run_dict(active_run)

            latest_run_no = session.scalar(select(func.max(AuditRun.run_no)).where(AuditRun.round_id == round_item.id)) or 0
            audit_run = AuditRun(round_id=round_item.id, run_no=latest_run_no + 1, status="queued")
            session.add(audit_run)
            session.flush()
            job = QueueJob(
                job_code=f"AUDIT-{audit_run.id}",
                job_type="audit",
                queue_name="audit",
                payload={"audit_run_id": str(audit_run.id), "round_id": str(round_item.id)},
                status="queued",
            )
            session.add(job)
            task.status = "auditing"
            round_item.status = "auditing"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_run",
                entity_id=audit_run.id,
                action_code="audit.start",
                after_snapshot={"round_id": str(round_item.id), "status": "queued"},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._audit_run_dict(audit_run, job_id=job.id)

    @staticmethod
    def _audit_run_dict(audit_run: AuditRun, job_id: UUID | None = None) -> dict[str, Any]:
        return {
            "id": str(audit_run.id),
            "audit_run_id": str(audit_run.id),
            "round_id": str(audit_run.round_id),
            "run_no": audit_run.run_no,
            "status": audit_run.status,
            "job_id": str(job_id) if job_id else None,
            "job_status": "queued",
            "created_at": _iso(audit_run.created_at),
            "updated_at": _iso(audit_run.updated_at),
        }

    def add_standard_to_round(self, round_id: str, payload: dict) -> dict | None:
        round_uuid = _uuid(round_id)
        version_uuid = _uuid(payload.get("standard_version_id"))
        if not round_uuid or not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            round_item = session.get(AuditRound, round_uuid)
            version = session.get(StandardVersion, version_uuid)
            if not round_item or not version:
                return None
            revision = session.scalar(
                select(StandardParseRevision)
                .where(StandardParseRevision.standard_version_id == version.id, StandardParseRevision.status == "published")
                .order_by(StandardParseRevision.created_at.desc(), StandardParseRevision.revision_no.desc())
                .limit(1)
            )
            if not revision:
                return None
            existing = session.scalar(
                select(RoundStandard).where(
                    RoundStandard.round_id == round_item.id,
                    RoundStandard.standard_version_id == version.id,
                )
            )
            if existing:
                return self._round_standard_dict(session, existing)
            selected = RoundStandard(
                round_id=round_item.id,
                standard_version_id=version.id,
                parse_revision_id=revision.id,
                source_type=payload.get("source_type", "document_reference"),
                selected_by_user_id=_uuid(payload.get("_operator_user_id")),
                snapshot_no=f"DRAFT-R{round_item.round_no}",
            )
            session.add(selected)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="round_standard",
                entity_id=selected.id,
                action_code="round_standard.select",
                after_snapshot={"round_id": str(round_item.id), "standard_version_id": str(version.id)},
                trace_id=payload.get("_trace_id"),
            )
            return self._round_standard_dict(session, selected)

    def list_round_standards(self, round_id: str) -> list[dict[str, Any]] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            return [
                self._round_standard_dict(session, item)
                for item in session.scalars(
                    select(RoundStandard).where(RoundStandard.round_id == round_uuid).order_by(RoundStandard.created_at)
                )
            ]

    def confirm_round_standard(self, round_id: str, round_standard_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        item_uuid = _uuid(round_standard_id)
        if not round_uuid or not item_uuid:
            return None
        with self.session_factory() as session, session.begin():
            round_item = session.get(AuditRound, round_uuid)
            selected = session.get(RoundStandard, item_uuid)
            if not round_item or not selected or selected.round_id != round_item.id:
                return None
            selected.snapshot_no = f"SNAPSHOT-R{round_item.round_no}"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="round_standard",
                entity_id=selected.id,
                action_code="round_standard.confirm",
                after_snapshot={"snapshot_no": selected.snapshot_no},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._round_standard_dict(session, selected)

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
