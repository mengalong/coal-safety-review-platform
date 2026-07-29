from __future__ import annotations

import json
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.auth import hash_password, verify_password
from coal_platform.config import get_settings
from coal_platform.model_security import ModelSecretCipher
from coal_platform.models import (
    AuditIssue,
    AuditRound,
    AuditRun,
    AuditTask,
    AuthSession,
    DynamicAuditItem,
    ExecutorDefinition,
    ExecutorVersion,
    IssueEvidence,
    IssueSource,
    ModelCallLog,
    ModelConfig,
    ModelProvider,
    OperationLog,
    ParsedBlock,
    QueueJob,
    Report,
    RoundRule,
    RoundStandard,
    RoundStandardCoverage,
    RuleDefinition,
    RuleExecution,
    RuleExecutionAttempt,
    RuleImpactAnalysis,
    RulePack,
    RulePackItem,
    RuleVersion,
    Standard,
    StandardClause,
    StandardParseRevision,
    StandardVersion,
    SystemAlert,
    SystemParameter,
    TaskFile,
    User,
)
from coal_platform.parse_quality import evaluate_parse_quality
from coal_platform.rule_engine import (
    DEFAULT_RULE_PACKS,
    DEFAULT_RULE_STAGE_BY_CODE,
    EXECUTOR_PARAMETER_SCHEMAS,
    FIXED_AUDIT_STAGE_ORDER,
    RuleConfigurationError,
    evaluate_trigger_condition,
    validate_dependency_graph,
    validate_parameters,
    validate_stage_code,
    validate_trigger_condition,
)
from coal_platform.store import DemoStore, compare_clause_sets, next_version_no


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
        self._model_cipher = ModelSecretCipher(get_settings().model_secret_key.get_secret_value())

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
                self._seed_rule_catalog(session)
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
            self._seed_rule_catalog(session)

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

    def _seed_rule_catalog(self, session: Session) -> None:
        if not session.scalar(select(ExecutorDefinition.id).limit(1)):
            for item in self.executors.values():
                definition = ExecutorDefinition(
                    executor_code=item["executor_code"],
                    executor_name=item["executor_name"],
                    executor_kind=item["executor_kind"],
                    input_type=item["input_type"],
                    output_type=item["output_type"],
                    runtime_mode="worker",
                    status="published" if item["status"] == "published" else item["status"],
                )
                session.add(definition)
                session.flush()
                version = ExecutorVersion(
                    executor_definition_id=definition.id,
                    version_no=item["version_no"],
                    parameter_schema={
                        **EXECUTOR_PARAMETER_SCHEMAS.get(item["executor_code"], {"type": "object"}),
                        "description": item.get("parameter_note", ""),
                    },
                    result_schema={"type": "object"},
                    default_timeout_seconds=item.get("default_timeout_seconds", 60),
                    supports_batch=item.get("supports_batch", False),
                    entrypoint=item.get("entrypoint"),
                    image_version="worker-demo",
                    status="published",
                )
                session.add(version)
                session.flush()

        executor_definitions = {
            item.executor_code: item for item in session.scalars(select(ExecutorDefinition))
        }
        executor_versions = {
            item.executor_definition_id: item
            for item in session.scalars(
                select(ExecutorVersion).where(ExecutorVersion.status == "published")
            )
        }
        if not session.scalar(select(RuleDefinition.id).limit(1)):
            for item in self.rules.values():
                executor_definition = executor_definitions.get(item["executor_code"])
                executor_version = executor_versions.get(executor_definition.id) if executor_definition else None
                if not executor_definition or not executor_version:
                    continue
                definition = RuleDefinition(
                    rule_code=item["rule_code"],
                    rule_name=item["rule_name"],
                    rule_type=item["rule_type"],
                    executor_definition_id=executor_definition.id,
                    default_issue_category=item.get("default_issue_category", "technical_compliance"),
                    default_severity=item.get("default_severity", item.get("severity", "一般")),
                    affects_suggested_conclusion=item.get("affects_suggested_conclusion", False),
                    is_mandatory=item.get("is_mandatory", False),
                )
                session.add(definition)
                session.flush()
                session.add(
                    RuleVersion(
                        rule_definition_id=definition.id,
                        version_no=item.get("version_no", "v1.0"),
                        executor_version_id=executor_version.id,
                        parameters={},
                        scope_files=[],
                        priority=100,
                        stage_code=DEFAULT_RULE_STAGE_BY_CODE.get(item["rule_code"], "standard_compliance"),
                        dependency_rule_codes=[],
                        task_override_allowed=True,
                        status="published",
                    )
                )
            session.flush()

        dynamic_definition = session.scalar(
            select(RuleDefinition).where(RuleDefinition.rule_code == "DYNAMIC_STANDARD_CLAUSE_REVIEW")
        )
        if not dynamic_definition:
            semantic_executor = executor_definitions.get("semantic_compare")
            semantic_version = executor_versions.get(semantic_executor.id) if semantic_executor else None
            if semantic_executor and semantic_version:
                dynamic_definition = RuleDefinition(
                    rule_code="DYNAMIC_STANDARD_CLAUSE_REVIEW",
                    rule_name="动态标准条款语义审核",
                    rule_type="ai",
                    executor_definition_id=semantic_executor.id,
                    default_issue_category="standard_compliance",
                    default_severity="一般",
                    affects_suggested_conclusion=True,
                    is_mandatory=True,
                )
                session.add(dynamic_definition)
                session.flush()
                session.add(
                    RuleVersion(
                        rule_definition_id=dynamic_definition.id,
                        version_no="v1.0",
                        executor_version_id=semantic_version.id,
                        parameters={"minimum_confidence": 0.65},
                        scope_files=[],
                        priority=500,
                        stage_code="standard_compliance",
                        dependency_rule_codes=[],
                        task_override_allowed=False,
                        status="published",
                    )
                )
                session.flush()

        if session.scalar(select(RulePack.id).limit(1)):
            return
        definitions = {item.rule_code: item for item in session.scalars(select(RuleDefinition))}
        for rule_code, stage_code in DEFAULT_RULE_STAGE_BY_CODE.items():
            definition = definitions.get(rule_code)
            if not definition:
                continue
            published = session.scalar(
                select(RuleVersion)
                .where(RuleVersion.rule_definition_id == definition.id, RuleVersion.status == "published")
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            if published:
                published.stage_code = stage_code
        versions = {
            definition.rule_code: session.scalar(
                select(RuleVersion)
                .where(RuleVersion.rule_definition_id == definition.id, RuleVersion.status == "published")
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            for definition in definitions.values()
        }
        for config in DEFAULT_RULE_PACKS:
            pack = RulePack(
                pack_code=config["pack_code"],
                pack_name=config["pack_name"],
                stage_code=config["stage_code"],
                trigger_condition=config["trigger_condition"],
                status="published",
            )
            session.add(pack)
            session.flush()
            for order_no, rule_code in enumerate(config["rule_codes"], start=1):
                version = versions.get(rule_code)
                if version:
                    session.add(RulePackItem(rule_pack_id=pack.id, rule_version_id=version.id, order_no=order_no, enabled=True))

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
        rules = []
        if session:
            standards = [
                self._round_standard_dict(session, item)
                for item in session.scalars(
                    select(RoundStandard).where(RoundStandard.round_id == round_item.id).order_by(RoundStandard.created_at)
                )
            ]
            rules = [
                self._round_rule_dict(session, item)
                for item in session.scalars(
                    select(RoundRule).where(RoundRule.round_id == round_item.id).order_by(RoundRule.created_at)
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
            "rules": rules,
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
            "parse_summary": file_item.parse_summary,
            "is_required": file_item.is_required,
            "is_applicable": file_item.is_applicable,
            "created_at": _iso(file_item.created_at),
            "updated_at": _iso(file_item.updated_at),
        }

    @staticmethod
    def _executor_version_dict(session: Session, version: ExecutorVersion) -> dict[str, Any]:
        definition = session.get(ExecutorDefinition, version.executor_definition_id)
        return {
            "id": str(version.id),
            "executor_definition_id": str(version.executor_definition_id),
            "executor_code": definition.executor_code if definition else None,
            "version_no": version.version_no,
            "parameter_schema": version.parameter_schema,
            "result_schema": version.result_schema,
            "default_timeout_seconds": version.default_timeout_seconds,
            "supports_batch": version.supports_batch,
            "entrypoint": version.entrypoint,
            "image_version": version.image_version,
            "status": version.status,
        }

    def _executor_dict(self, session: Session, definition: ExecutorDefinition) -> dict[str, Any]:
        versions = session.scalars(
            select(ExecutorVersion)
            .where(ExecutorVersion.executor_definition_id == definition.id)
            .order_by(ExecutorVersion.created_at.desc())
        ).all()
        latest = versions[0] if versions else None
        return {
            "id": str(definition.id),
            "executor_code": definition.executor_code,
            "executor_name": definition.executor_name,
            "executor_kind": definition.executor_kind,
            "input_type": definition.input_type,
            "output_type": definition.output_type,
            "runtime_mode": definition.runtime_mode,
            "status": definition.status,
            "version_no": latest.version_no if latest else None,
            "default_timeout_seconds": latest.default_timeout_seconds if latest else None,
            "supports_batch": latest.supports_batch if latest else None,
            "entrypoint": latest.entrypoint if latest else None,
            "versions": [self._executor_version_dict(session, version) for version in versions],
        }

    def _rule_version_dict(self, session: Session, version: RuleVersion) -> dict[str, Any]:
        rule = session.get(RuleDefinition, version.rule_definition_id)
        executor_version = session.get(ExecutorVersion, version.executor_version_id)
        executor = session.get(ExecutorDefinition, executor_version.executor_definition_id) if executor_version else None
        return {
            "id": str(version.id),
            "rule_definition_id": str(version.rule_definition_id),
            "rule_code": rule.rule_code if rule else None,
            "rule_name": rule.rule_name if rule else None,
            "version_no": version.version_no,
            "executor_version_id": str(version.executor_version_id),
            "executor_code": executor.executor_code if executor else None,
            "parameters": version.parameters,
            "scope_files": version.scope_files,
            "priority": version.priority,
            "stage_code": version.stage_code,
            "dependency_rule_codes": version.dependency_rule_codes or [],
            "task_override_allowed": version.task_override_allowed,
            "status": version.status,
        }

    def _rule_dict(self, session: Session, definition: RuleDefinition) -> dict[str, Any]:
        versions = session.scalars(
            select(RuleVersion)
            .where(RuleVersion.rule_definition_id == definition.id)
            .order_by(RuleVersion.created_at.desc())
        ).all()
        executor = session.get(ExecutorDefinition, definition.executor_definition_id)
        latest = versions[0] if versions else None
        return {
            "id": str(definition.id),
            "rule_code": definition.rule_code,
            "rule_name": definition.rule_name,
            "rule_type": definition.rule_type,
            "executor_definition_id": str(definition.executor_definition_id),
            "executor_code": executor.executor_code if executor else None,
            "default_issue_category": definition.default_issue_category,
            "default_severity": definition.default_severity,
            "severity": definition.default_severity,
            "affects_suggested_conclusion": definition.affects_suggested_conclusion,
            "is_mandatory": definition.is_mandatory,
            "version_no": latest.version_no if latest else None,
            "status": latest.status if latest else "draft",
            "versions": [self._rule_version_dict(session, version) for version in versions],
        }

    def _rule_pack_dict(self, session: Session, pack: RulePack) -> dict[str, Any]:
        items = session.scalars(
            select(RulePackItem).where(RulePackItem.rule_pack_id == pack.id).order_by(RulePackItem.order_no)
        ).all()
        version_ids = [item.rule_version_id for item in items]
        versions = {
            item.id: item for item in session.scalars(select(RuleVersion).where(RuleVersion.id.in_(version_ids)))
        } if version_ids else {}
        return {
            "id": str(pack.id),
            "pack_code": pack.pack_code,
            "pack_name": pack.pack_name,
            "stage_code": pack.stage_code,
            "trigger_condition": pack.trigger_condition or {},
            "source_type": (pack.trigger_condition or {}).get("source_type", "global"),
            "status": pack.status,
            "items": [
                {
                    "id": str(item.id),
                    "rule_pack_id": str(item.rule_pack_id),
                    "rule_version_id": str(item.rule_version_id),
                    "rule_code": self._rule_version_dict(session, versions[item.rule_version_id])["rule_code"]
                    if item.rule_version_id in versions
                    else None,
                    "version_no": versions[item.rule_version_id].version_no if item.rule_version_id in versions else None,
                    "order_no": item.order_no,
                    "enabled": item.enabled,
                }
                for item in items
            ],
        }

    def _round_rule_dict(self, session: Session, item: RoundRule) -> dict[str, Any]:
        version = session.get(RuleVersion, item.rule_version_id)
        rule = session.get(RuleDefinition, version.rule_definition_id) if version else None
        executor_version = session.get(ExecutorVersion, item.executor_version_id)
        executor = session.get(ExecutorDefinition, executor_version.executor_definition_id) if executor_version else None
        return {
            "id": str(item.id),
            "round_id": str(item.round_id),
            "rule_version_id": str(item.rule_version_id),
            "executor_version_id": str(item.executor_version_id),
            "rule_code": rule.rule_code if rule else None,
            "rule_name": rule.rule_name if rule else None,
            "executor_code": executor.executor_code if executor else None,
            "stage_code": version.stage_code if version else None,
            "source_type": item.source_type,
            "enable_reason": "全局基础规则" if item.source_type == "global" else "任务文件和数据满足触发条件",
            "enabled": item.enabled,
            "override_payload": item.override_payload,
            "disable_reason": item.disable_reason,
            "snapshot_no": item.snapshot_no,
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

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        user_uuid = _uuid(user_id)
        if not user_uuid:
            return False
        context = payload or {}
        with self.session_factory() as session, session.begin():
            user = session.scalar(select(User).where(User.id == user_uuid).with_for_update())
            if not user or not verify_password(current_password, user.password_hash) or current_password == new_password:
                return False
            user.password_hash = hash_password(new_password)
            revoked_at = datetime.now(UTC)
            active_sessions = list(
                session.scalars(
                    select(AuthSession).where(
                        AuthSession.user_id == user_uuid,
                        AuthSession.status == "active",
                    )
                )
            )
            for auth_session in active_sessions:
                auth_session.status = "revoked"
                auth_session.revoked_at = revoked_at
            self._log(
                session,
                operator_user_id=user.id,
                entity_type="user",
                entity_id=user.id,
                action_code="user.password.change",
                after_snapshot={"sessions_revoked": len(active_sessions)},
                trace_id=context.get("_trace_id"),
            )
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

    def list_executors(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            definitions = session.scalars(select(ExecutorDefinition).order_by(ExecutorDefinition.executor_code)).all()
            return [self._executor_dict(session, item) for item in definitions]

    def create_executor(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            if session.scalar(
                select(ExecutorDefinition.id).where(ExecutorDefinition.executor_code == payload["executor_code"])
            ):
                return None
            definition = ExecutorDefinition(
                executor_code=payload["executor_code"],
                executor_name=payload["executor_name"],
                executor_kind=payload.get("executor_kind", "builtin"),
                input_type=payload.get("input_type", "rule_input"),
                output_type=payload.get("output_type", "rule_result"),
                runtime_mode=payload.get("runtime_mode", "worker"),
                status=payload.get("status", "draft"),
            )
            session.add(definition)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="executor_definition",
                entity_id=definition.id,
                action_code="executor.create",
                after_snapshot={"executor_code": definition.executor_code},
                trace_id=payload.get("_trace_id"),
            )
            return self._executor_dict(session, definition)

    def list_executor_versions(self, executor_code: str) -> list[dict[str, Any]] | None:
        with self.session_factory() as session:
            definition = session.scalar(
                select(ExecutorDefinition).where(ExecutorDefinition.executor_code == executor_code)
            )
            if not definition:
                return None
            versions = session.scalars(
                select(ExecutorVersion)
                .where(ExecutorVersion.executor_definition_id == definition.id)
                .order_by(ExecutorVersion.created_at.desc())
            ).all()
            return [self._executor_version_dict(session, item) for item in versions]

    def create_executor_version(self, executor_code: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            definition = session.scalar(
                select(ExecutorDefinition).where(ExecutorDefinition.executor_code == executor_code)
            )
            if not definition or session.scalar(
                select(ExecutorVersion.id).where(
                    ExecutorVersion.executor_definition_id == definition.id,
                    ExecutorVersion.version_no == payload["version_no"],
                )
            ):
                return None
            version = ExecutorVersion(
                executor_definition_id=definition.id,
                version_no=payload["version_no"],
                parameter_schema=payload.get("parameter_schema") or {},
                result_schema=payload.get("result_schema") or {},
                default_timeout_seconds=payload.get("default_timeout_seconds", 60),
                supports_batch=payload.get("supports_batch", False),
                entrypoint=payload.get("entrypoint"),
                image_version=payload.get("image_version"),
                status=payload.get("status", "draft"),
            )
            session.add(version)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="executor_version",
                entity_id=version.id,
                action_code="executor.version.create",
                after_snapshot={"executor_code": executor_code, "version_no": version.version_no},
                trace_id=payload.get("_trace_id"),
            )
            return self._executor_version_dict(session, version)

    def list_rules(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            definitions = session.scalars(select(RuleDefinition).order_by(RuleDefinition.rule_code)).all()
            return [self._rule_dict(session, item) for item in definitions]

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        rule_uuid = _uuid(rule_id)
        if not rule_uuid:
            return None
        with self.session_factory() as session:
            definition = session.get(RuleDefinition, rule_uuid)
            return self._rule_dict(session, definition) if definition else None

    def create_rule(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            if session.scalar(select(RuleDefinition.id).where(RuleDefinition.rule_code == payload["rule_code"])):
                return None
            executor = session.scalar(
                select(ExecutorDefinition).where(ExecutorDefinition.executor_code == payload["executor_code"])
            )
            if not executor:
                return None
            definition = RuleDefinition(
                rule_code=payload["rule_code"],
                rule_name=payload["rule_name"],
                rule_type=payload["rule_type"],
                executor_definition_id=executor.id,
                default_issue_category=payload["default_issue_category"],
                default_severity=payload["default_severity"],
                affects_suggested_conclusion=payload.get("affects_suggested_conclusion", False),
                is_mandatory=payload.get("is_mandatory", False),
            )
            session.add(definition)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="rule_definition",
                entity_id=definition.id,
                action_code="rule.create",
                after_snapshot={"rule_code": definition.rule_code},
                trace_id=payload.get("_trace_id"),
            )
            return self._rule_dict(session, definition)

    def list_rule_versions(self, rule_id: str) -> list[dict[str, Any]] | None:
        rule_uuid = _uuid(rule_id)
        if not rule_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(RuleDefinition, rule_uuid):
                return None
            versions = session.scalars(
                select(RuleVersion)
                .where(RuleVersion.rule_definition_id == rule_uuid)
                .order_by(RuleVersion.created_at.desc())
            ).all()
            return [self._rule_version_dict(session, item) for item in versions]

    def get_rule_version(self, version_id: str) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            version = session.get(RuleVersion, version_uuid)
            return self._rule_version_dict(session, version) if version else None

    def create_rule_version(self, rule_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        rule_uuid = _uuid(rule_id)
        if not rule_uuid:
            return None
        with self.session_factory() as session, session.begin():
            definition = session.get(RuleDefinition, rule_uuid)
            if not definition:
                return None
            stage_code = payload.get("stage_code", "standard_compliance")
            if errors := validate_stage_code(stage_code):
                raise RuleConfigurationError(errors)
            executor_version = None
            if payload.get("executor_version_id"):
                executor_version = session.get(ExecutorVersion, _uuid(payload["executor_version_id"]))
                if not executor_version or executor_version.executor_definition_id != definition.executor_definition_id:
                    return None
            else:
                executor_version = session.scalar(
                    select(ExecutorVersion)
                    .where(
                        ExecutorVersion.executor_definition_id == definition.executor_definition_id,
                        ExecutorVersion.status == "published",
                    )
                    .order_by(ExecutorVersion.created_at.desc())
                    .limit(1)
                )
            if not executor_version or executor_version.status != "published":
                return None
            existing_numbers = session.scalars(
                select(RuleVersion.version_no).where(RuleVersion.rule_definition_id == definition.id)
            ).all()
            version_no = payload.get("version_no") or next_version_no(existing_numbers)
            if version_no in existing_numbers:
                return None
            version = RuleVersion(
                rule_definition_id=definition.id,
                version_no=version_no,
                executor_version_id=executor_version.id,
                parameters=payload.get("parameters") or {},
                scope_files=payload.get("scope_files") or [],
                priority=payload.get("priority", 100),
                stage_code=stage_code,
                dependency_rule_codes=payload.get("dependency_rule_codes") or [],
                task_override_allowed=payload.get("task_override_allowed", True),
                status="draft",
            )
            session.add(version)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="rule_version",
                entity_id=version.id,
                action_code="rule_version.create",
                after_snapshot={"version_no": version.version_no, "rule_id": str(definition.id)},
                trace_id=payload.get("_trace_id"),
            )
            return self._rule_version_dict(session, version)

    def _rule_validation_errors(self, session: Session, version: RuleVersion) -> list[dict[str, Any]]:
        definition = session.get(RuleDefinition, version.rule_definition_id)
        executor_version = session.get(ExecutorVersion, version.executor_version_id)
        errors = validate_stage_code(version.stage_code)
        if not executor_version or executor_version.status != "published":
            errors.append({"code": "EXECUTOR_VERSION_UNAVAILABLE", "message": "executor version is not published", "path": "executor_version_id"})
        else:
            errors.extend(validate_parameters(version.parameters or {}, executor_version.parameter_schema or {}))
        graph: dict[str, list[str]] = {}
        known_rule_codes: set[str] = set()
        for item in session.scalars(select(RuleDefinition)):
            published = session.scalar(
                select(RuleVersion)
                .where(RuleVersion.rule_definition_id == item.id, RuleVersion.status == "published")
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            if published:
                graph[item.rule_code] = list(published.dependency_rule_codes or [])
                known_rule_codes.add(item.rule_code)
        if definition:
            graph[definition.rule_code] = list(version.dependency_rule_codes or [])
            known_rule_codes.add(definition.rule_code)
        errors.extend(validate_dependency_graph(graph, known_rule_codes))
        return errors

    def validate_rule_version(self, version_id: str) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            version = session.get(RuleVersion, version_uuid)
            if not version:
                return None
            errors = self._rule_validation_errors(session, version)
            return {"valid": not errors, "rule_version_id": version_id, "errors": errors}

    def publish_rule_version(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(RuleVersion, version_uuid)
            if not version:
                return None
            if errors := self._rule_validation_errors(session, version):
                raise RuleConfigurationError(errors)
            for previous in session.scalars(
                select(RuleVersion).where(
                    RuleVersion.rule_definition_id == version.rule_definition_id,
                    RuleVersion.status == "published",
                    RuleVersion.id != version.id,
                )
            ):
                previous.status = "archived"
            version.status = "published"
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="rule_version",
                entity_id=version.id,
                action_code="rule_version.publish",
                after_snapshot={"status": "published"},
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._rule_version_dict(session, version)

    def disable_rule_version(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(RuleVersion, version_uuid)
            if not version or version.status == "archived":
                return None
            version.status = "disabled"
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="rule_version", entity_id=version.id, action_code="rule_version.disable", after_snapshot={"status": "disabled"}, trace_id=payload.get("_trace_id"))
            session.flush()
            return self._rule_version_dict(session, version)

    def copy_rule_version(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session:
            source = session.get(RuleVersion, version_uuid)
            if not source:
                return None
            existing = session.scalars(select(RuleVersion.version_no).where(RuleVersion.rule_definition_id == source.rule_definition_id)).all()
            source_payload = {
                "version_no": payload.get("version_no") or next_version_no(existing),
                "parameters": source.parameters,
                "scope_files": source.scope_files,
                "priority": source.priority,
                "stage_code": source.stage_code,
                "dependency_rule_codes": source.dependency_rule_codes,
                "task_override_allowed": source.task_override_allowed,
                "executor_version_id": str(source.executor_version_id),
                **payload,
            }
            source_payload["_operator_user_id"] = payload.get("_operator_user_id")
            source_payload["_trace_id"] = payload.get("_trace_id")
        return self.create_rule_version(str(source.rule_definition_id), source_payload)

    def create_rule_test_run(self, version_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        version_uuid = _uuid(version_id)
        if not version_uuid:
            return None
        with self.session_factory() as session, session.begin():
            version = session.get(RuleVersion, version_uuid)
            if not version:
                return None
            if errors := self._rule_validation_errors(session, version):
                raise RuleConfigurationError(errors)
            definition = session.get(RuleDefinition, version.rule_definition_id)
            job = QueueJob(
                job_code=f"RULE-TEST-{uuid4().hex}",
                job_type="rule_test_run",
                queue_name="rule_test",
                status="queued",
                payload={
                    "rule_version_id": str(version.id),
                    "rule_code": definition.rule_code if definition else None,
                    "executor_version_id": str(version.executor_version_id),
                    "parameters": version.parameters or {},
                    "input_payload": payload.get("input_payload") or {},
                    "evidence": payload.get("evidence") or [],
                    "standard_evidence": payload.get("standard_evidence") or [],
                    "dry_run": True,
                },
            )
            session.add(job)
            session.flush()
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="queue_job", entity_id=job.id, action_code="rule_version.test_run.create", after_snapshot={"job_code": job.job_code, "rule_version_id": str(version.id)}, trace_id=payload.get("_trace_id"))
            return {
                "id": str(job.id), "job_code": job.job_code, "job_type": job.job_type,
                "queue_name": job.queue_name, "status": job.status, "retry_count": job.retry_count,
                "payload": job.payload, "created_at": _iso(job.created_at),
            }

    @staticmethod
    def _queue_job_dict(job: QueueJob) -> dict[str, Any]:
        payload = job.payload or {}
        return {
            "id": str(job.id), "job_code": job.job_code, "job_type": job.job_type,
            "queue_name": job.queue_name,
            "payload": {key: value for key, value in payload.items() if key not in {"result", "error"}},
            "result": payload.get("result"), "error": payload.get("error"), "status": job.status,
            "retry_count": job.retry_count, "scheduled_at": _iso(job.scheduled_at),
            "started_at": _iso(job.started_at), "finished_at": _iso(job.finished_at),
            "created_at": _iso(job.created_at), "updated_at": _iso(job.updated_at),
        }

    def list_queue_jobs(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [self._queue_job_dict(item) for item in session.scalars(select(QueueJob).order_by(QueueJob.created_at.desc()))]

    def list_alerts(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [{"id": str(item.id), "alert_code": item.alert_code, "severity": item.severity, "source_type": item.source_type, "source_id": str(item.source_id) if item.source_id else None, "title": item.title, "detail": item.detail, "status": item.status, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)} for item in session.scalars(select(SystemAlert).order_by(SystemAlert.created_at.desc()))]

    @staticmethod
    def _model_config_dict(session: Session, item: ModelConfig) -> dict[str, Any]:
        provider = session.get(ModelProvider, item.provider_id)
        return {"id": str(item.id), "provider_code": provider.provider_code if provider else None, "provider_name": provider.provider_name if provider else None, "base_url": provider.base_url if provider else None, "model_code": item.model_code, "model_kind": item.model_kind, "api_key_configured": bool(item.api_key_ciphertext), "credential_version": item.credential_version, "key_rotated_at": _iso(item.key_rotated_at), "timeout_seconds": item.timeout_seconds, "concurrency_limit": item.concurrency_limit, "status": item.status, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}

    def list_model_configs(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [self._model_config_dict(session, item) for item in session.scalars(select(ModelConfig).order_by(ModelConfig.created_at.desc()))]

    def create_model_config(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            provider = session.scalar(select(ModelProvider).where(ModelProvider.provider_code == payload["provider_code"]))
            if not provider:
                provider = ModelProvider(provider_code=payload["provider_code"], provider_name=payload["provider_name"], base_url=payload["base_url"], status="active")
                session.add(provider)
                session.flush()
            if session.scalar(select(ModelConfig.id).where(ModelConfig.model_code == payload["model_code"], ModelConfig.provider_id == provider.id)):
                return None
            item = ModelConfig(provider_id=provider.id, model_code=payload["model_code"], model_kind=payload["model_kind"], api_key_ciphertext=self._model_cipher.encrypt(payload["api_key"]), credential_version=1, key_rotated_at=datetime.now(UTC), timeout_seconds=payload.get("timeout_seconds", 60), concurrency_limit=payload.get("concurrency_limit", 1), status="active")
            session.add(item)
            session.flush()
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="model_config", entity_id=item.id, action_code="model_config.create", after_snapshot=self._model_config_dict(session, item), trace_id=payload.get("_trace_id"))
            return self._model_config_dict(session, item)

    def update_model_config(self, config_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        config_uuid = _uuid(config_id)
        if not config_uuid:
            return None
        with self.session_factory() as session, session.begin():
            item = session.get(ModelConfig, config_uuid)
            if not item:
                return None
            if payload.get("api_key"):
                item.api_key_ciphertext = self._model_cipher.encrypt(payload["api_key"])
                item.credential_version += 1
                item.key_rotated_at = datetime.now(UTC)
            for key in ("timeout_seconds", "concurrency_limit", "status"):
                if payload.get(key) is not None:
                    setattr(item, key, payload[key])
            session.flush()
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="model_config", entity_id=item.id, action_code="model_config.update", after_snapshot=self._model_config_dict(session, item), trace_id=payload.get("_trace_id"))
            return self._model_config_dict(session, item)

    def get_model_runtime_config(self, config_id: str) -> dict[str, Any] | None:
        config_uuid = _uuid(config_id)
        if not config_uuid:
            return None
        with self.session_factory() as session:
            item = session.get(ModelConfig, config_uuid)
            if not item:
                return None
            provider = session.get(ModelProvider, item.provider_id)
            if not provider:
                return None
            return {**self._model_config_dict(session, item), "api_key": self._model_cipher.decrypt(item.api_key_ciphertext)}

    def record_model_call(self, payload: dict[str, Any]) -> None:
        with self.session_factory() as session, session.begin():
            session.add(ModelCallLog(model_config_id=_uuid(payload["model_config_id"]), request_id=payload["request_id"], trace_id=payload.get("trace_id"), operation=payload["operation"], status=payload["status"], attempt_count=payload.get("attempt_count", 1), latency_ms=payload["latency_ms"], http_status=payload.get("http_status"), provider_request_id=payload.get("provider_request_id"), token_usage=payload.get("token_usage") or {}, error_code=payload.get("error_code")))

    def list_model_call_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            items = session.scalars(select(ModelCallLog).order_by(ModelCallLog.created_at.desc()).limit(limit)).all()
            return [{"id": str(item.id), "model_config_id": str(item.model_config_id), "request_id": item.request_id, "trace_id": item.trace_id, "operation": item.operation, "status": item.status, "attempt_count": item.attempt_count, "latency_ms": item.latency_ms, "http_status": item.http_status, "provider_request_id": item.provider_request_id, "token_usage": item.token_usage, "error_code": item.error_code, "created_at": _iso(item.created_at)} for item in items]

    @staticmethod
    def _system_parameter_dict(item: SystemParameter) -> dict[str, Any]:
        return {"id": str(item.id), "param_key": item.param_key, "param_value": item.param_value, "scope": item.scope, "status": item.status, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}

    def list_system_parameters(self) -> list[dict[str, Any]]:
        return self.list_config_entries("global")

    def list_config_entries(self, scope: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [self._system_parameter_dict(item) for item in session.scalars(select(SystemParameter).where(SystemParameter.scope == scope).order_by(SystemParameter.param_key))]

    def upsert_config_entry(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            item = session.scalar(select(SystemParameter).where(SystemParameter.param_key == key))
            if not item:
                item = SystemParameter(param_key=key, param_value=payload.get("param_value") or {}, scope=payload.get("scope", "global"), status=payload.get("status", "active"))
                session.add(item)
            else:
                item.param_value = payload.get("param_value") or {}
                item.scope = payload.get("scope", item.scope)
                item.status = payload.get("status", item.status)
            session.flush()
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="system_parameter", entity_id=item.id, action_code="system_parameter.upsert", after_snapshot=self._system_parameter_dict(item), trace_id=payload.get("_trace_id"))
            return self._system_parameter_dict(item)

    def get_queue_job(self, job_id: str) -> dict[str, Any] | None:
        job_uuid = _uuid(job_id)
        if not job_uuid:
            return None
        with self.session_factory() as session:
            job = session.get(QueueJob, job_uuid)
            return self._queue_job_dict(job) if job else None

    def update_queue_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        job_uuid = _uuid(job_id)
        if not job_uuid:
            return None
        with self.session_factory() as session, session.begin():
            job = session.get(QueueJob, job_uuid)
            if not job:
                return None
            job.status = payload.get("status", job.status)
            if "result" in payload or "error" in payload:
                job.payload = {**(job.payload or {}), "result": payload.get("result"), "error": payload.get("error")}
            if job.status == "running" and not job.started_at:
                job.started_at = datetime.now(UTC)
            if job.status in {"succeeded", "failed"}:
                job.finished_at = datetime.now(UTC)
            session.flush()
            return self._queue_job_dict(job)

    def retry_queue_job(self, job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        job_uuid = _uuid(job_id)
        if not job_uuid:
            return None
        with self.session_factory() as session, session.begin():
            job = session.get(QueueJob, job_uuid)
            if not job or job.status not in {"failed", "exception"}:
                return None
            job.retry_count += 1
            job.status = "queued" if job.retry_count <= 3 else "failed"
            if payload and payload.get("error"):
                job.payload = {**(job.payload or {}), "error": payload["error"]}
            session.flush()
            return self._queue_job_dict(job)

    def cancel_queue_job(self, job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        job_uuid = _uuid(job_id)
        if not job_uuid:
            return None
        context = payload or {}
        with self.session_factory() as session, session.begin():
            job = session.scalar(select(QueueJob).where(QueueJob.id == job_uuid).with_for_update())
            if not job or job.status not in {"queued", "pending"}:
                return None
            before = self._queue_job_dict(job)
            job.status = "canceled"
            job.finished_at = datetime.now(UTC)
            session.flush()
            after = self._queue_job_dict(job)
            self._log(
                session,
                operator_user_id=context.get("_operator_user_id"),
                entity_type="queue_job",
                entity_id=job.id,
                action_code="queue_job.cancel",
                before_snapshot=before,
                after_snapshot=after,
                trace_id=context.get("_trace_id"),
            )
            return after

    def complete_audit_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        run_uuid = _uuid(run_id)
        if not run_uuid:
            return None
        with self.session_factory() as session, session.begin():
            run = session.get(AuditRun, run_uuid)
            if not run:
                return None
            run.status = payload.get("status", "succeeded")
            run.summary = payload.get("summary") or {}
            run.finished_at = datetime.now(UTC)
            round_item = session.get(AuditRound, run.round_id)
            task = session.get(AuditTask, round_item.task_id) if round_item else None
            if round_item:
                round_item.status = "waiting_review"
            if task:
                task.status = "waiting_review"
            session.flush()
            return self._audit_run_dict(run)

    def list_rule_packs(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            packs = session.scalars(select(RulePack)).all()
            packs.sort(key=lambda item: (FIXED_AUDIT_STAGE_ORDER[item.stage_code], item.pack_code))
            return [self._rule_pack_dict(session, item) for item in packs]

    def _validate_rule_pack_payload(
        self,
        session: Session,
        payload: dict[str, Any],
        member_ids: list[str],
    ) -> list[dict[str, Any]]:
        errors = validate_stage_code(payload.get("stage_code", ""))
        errors.extend(validate_trigger_condition(payload.get("trigger_condition") or {}))
        if payload.get("status", "draft") not in {"draft", "published", "disabled", "archived"}:
            errors.append({"code": "INVALID_PACK_STATUS", "message": "unknown rule pack status", "path": "status"})
        if payload.get("status") == "published" and not member_ids:
            errors.append({"code": "EMPTY_RULE_PACK", "message": "published rule pack must contain rules", "path": "rule_version_ids"})
        for index, version_id in enumerate(member_ids):
            version_uuid = _uuid(version_id)
            version = session.get(RuleVersion, version_uuid) if version_uuid else None
            definition = session.get(RuleDefinition, version.rule_definition_id) if version else None
            if not version or not definition:
                errors.append({"code": "RULE_VERSION_NOT_FOUND", "message": "rule version not found", "path": f"rule_version_ids.{index}"})
                continue
            if version.status != "published":
                errors.append({"code": "RULE_VERSION_NOT_PUBLISHED", "message": "rule pack only accepts published rule versions", "path": f"rule_version_ids.{index}"})
            if version.stage_code != payload.get("stage_code"):
                errors.append({"code": "STAGE_MISMATCH", "message": "rule version stage does not match rule pack stage", "path": f"rule_version_ids.{index}"})
        return errors

    def create_rule_pack(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            if session.scalar(select(RulePack.id).where(RulePack.pack_code == payload["pack_code"])):
                return None
            member_ids = payload.get("rule_version_ids") or []
            if errors := self._validate_rule_pack_payload(session, payload, member_ids):
                raise RuleConfigurationError(errors)
            pack = RulePack(
                pack_code=payload["pack_code"],
                pack_name=payload["pack_name"],
                stage_code=payload["stage_code"],
                trigger_condition=payload.get("trigger_condition") or {},
                status=payload.get("status", "draft"),
            )
            session.add(pack)
            session.flush()
            for order_no, version_id in enumerate(member_ids, start=1):
                session.add(
                    RulePackItem(
                        rule_pack_id=pack.id,
                        rule_version_id=_uuid(version_id),
                        order_no=order_no,
                        enabled=True,
                    )
                )
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="rule_pack",
                entity_id=pack.id,
                action_code="rule_pack.create",
                after_snapshot={"pack_code": pack.pack_code, "rule_version_ids": member_ids},
                trace_id=payload.get("_trace_id"),
            )
            return self._rule_pack_dict(session, pack)

    def update_rule_pack(self, pack_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        pack_uuid = _uuid(pack_id)
        if not pack_uuid:
            return None
        with self.session_factory() as session, session.begin():
            pack = session.get(RulePack, pack_uuid)
            if not pack:
                return None
            before = {
                "pack_name": pack.pack_name,
                "stage_code": pack.stage_code,
                "trigger_condition": pack.trigger_condition,
                "status": pack.status,
            }
            for key in ("pack_name", "stage_code", "trigger_condition", "status"):
                if payload.get(key) is not None:
                    setattr(pack, key, payload[key])
            existing_items = session.scalars(select(RulePackItem).where(RulePackItem.rule_pack_id == pack.id)).all()
            member_ids = payload.get("rule_version_ids")
            if member_ids is None:
                member_ids = [str(item.rule_version_id) for item in existing_items]
            pack_payload = {
                "stage_code": pack.stage_code,
                "trigger_condition": pack.trigger_condition or {},
                "status": pack.status,
            }
            if errors := self._validate_rule_pack_payload(session, pack_payload, member_ids):
                raise RuleConfigurationError(errors)
            for item in existing_items:
                session.delete(item)
            session.flush()
            for order_no, version_id in enumerate(member_ids, start=1):
                session.add(
                    RulePackItem(
                        rule_pack_id=pack.id,
                        rule_version_id=_uuid(version_id),
                        order_no=order_no,
                        enabled=True,
                    )
                )
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="rule_pack",
                entity_id=pack.id,
                action_code="rule_pack.update",
                before_snapshot=before,
                after_snapshot={
                    "pack_name": pack.pack_name,
                    "stage_code": pack.stage_code,
                    "trigger_condition": pack.trigger_condition,
                    "status": pack.status,
                    "rule_version_ids": member_ids,
                },
                trace_id=payload.get("_trace_id"),
            )
            return self._rule_pack_dict(session, pack)

    def list_round_rules(self, round_id: str) -> list[dict[str, Any]] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            return [
                self._round_rule_dict(session, item)
                for item in session.scalars(
                    select(RoundRule).where(RoundRule.round_id == round_uuid).order_by(RoundRule.created_at)
                )
            ]

    def assemble_round_rules(self, round_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session, session.begin():
            round_item = session.scalar(select(AuditRound).where(AuditRound.id == round_uuid).with_for_update())
            if not round_item:
                return None
            existing = session.scalars(
                select(RoundRule).where(RoundRule.round_id == round_item.id).order_by(RoundRule.created_at)
            ).all()
            if existing:
                return {
                    "round_id": round_id,
                    "snapshot_no": existing[0].snapshot_no,
                    "locked": True,
                    "rules": [self._round_rule_dict(session, item) for item in existing],
                }
            task = session.get(AuditTask, round_item.task_id)
            if not task:
                return None
            requested_ids = payload.get("rule_pack_ids", [])
            selected_ids = {_uuid(item) for item in requested_ids if _uuid(item)}
            if requested_ids and len(selected_ids) != len(set(requested_ids)):
                raise RuleConfigurationError(
                    [{"code": "RULE_PACK_NOT_FOUND", "message": "rule pack id is invalid", "path": "rule_pack_ids"}]
                )
            if selected_ids:
                found_ids = set(session.scalars(select(RulePack.id).where(RulePack.id.in_(selected_ids))))
                missing_ids = selected_ids - found_ids
                if missing_ids:
                    raise RuleConfigurationError(
                        [
                            {
                                "code": "RULE_PACK_NOT_FOUND",
                                "message": f"rule pack not found: {pack_id}",
                                "path": "rule_pack_ids",
                            }
                            for pack_id in sorted(missing_ids, key=str)
                        ]
                    )
            pack_query = select(RulePack).where(RulePack.status == "published")
            if selected_ids:
                pack_query = pack_query.where(RulePack.id.in_(selected_ids))
            packs = session.scalars(pack_query.order_by(RulePack.stage_code, RulePack.pack_code)).all()
            file_types = list(
                session.scalars(select(TaskFile.file_type).where(TaskFile.task_id == task.id))
            )
            confirmed_standard_count = session.scalar(
                select(func.count(RoundStandard.id)).where(
                    RoundStandard.round_id == round_item.id,
                    RoundStandard.snapshot_no.like("SNAPSHOT-%"),
                )
            ) or 0
            candidate_ids: set[UUID] = set()
            candidates: list[tuple[RuleVersion, str, str]] = []
            skipped_packs = []
            for pack in packs:
                enabled, reason = evaluate_trigger_condition(
                    pack.trigger_condition or {},
                    file_types=file_types,
                    confirmed_standard_count=confirmed_standard_count,
                )
                if not enabled:
                    skipped_packs.append({"pack_code": pack.pack_code, "reason": reason})
                    continue
                members = session.scalars(
                    select(RulePackItem).where(RulePackItem.rule_pack_id == pack.id, RulePackItem.enabled.is_(True)).order_by(RulePackItem.order_no)
                ).all()
                for member in members:
                    if member.rule_version_id in candidate_ids:
                        continue
                    version = session.get(RuleVersion, member.rule_version_id)
                    if not version:
                        continue
                    candidate_ids.add(version.id)
                    candidates.append((version, (pack.trigger_condition or {}).get("source_type", "global"), reason))
            candidates.sort(
                key=lambda item: (
                    FIXED_AUDIT_STAGE_ORDER[item[0].stage_code],
                    item[0].priority,
                    str(item[0].id),
                )
            )
            snapshot_no = f"RULE-SNAPSHOT-R{round_item.round_no}-{str(round_item.id)[:8]}"
            created = []
            for version, source_type, _reason in candidates:
                item = RoundRule(
                    round_id=round_item.id,
                    rule_version_id=version.id,
                    executor_version_id=version.executor_version_id,
                    source_type=source_type,
                    enabled=True,
                    snapshot_no=snapshot_no,
                )
                session.add(item)
                created.append(item)
            session.flush()
            round_item.rule_snapshot_id = created[0].id if created else None
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_round",
                entity_id=round_item.id,
                action_code="round_rule.assemble",
                after_snapshot={"snapshot_no": snapshot_no, "rule_count": len(created)},
                trace_id=payload.get("_trace_id"),
            )
            return {
                "round_id": round_id,
                "snapshot_no": snapshot_no,
                "locked": True,
                "rules": [self._round_rule_dict(session, item) for item in created],
                "skipped_packs": skipped_packs,
            }

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
        if session.get_bind().dialect.name == "postgresql":
            # Serialize yearly human-readable number allocation across every API process.
            session.execute(select(func.pg_advisory_xact_lock(0x434F414C + year)))
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

    def update_task_file(self, task_id: str, file_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._mutate_task_file(task_id, file_id, payload, "task_file.update")

    def replace_task_file(self, task_id: str, file_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        payload = {**payload, "_replace": True}
        return self._mutate_task_file(task_id, file_id, payload, "task_file.replace")

    def delete_task_file(self, task_id: str, file_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._mutate_task_file(task_id, file_id, payload, "task_file.delete")

    def retry_task_file_parse(self, task_id: str, file_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._mutate_task_file(task_id, file_id, payload, "task_file.parse_retry")

    def create_task_file_parse_job(
        self, task_id: str, file_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        task_uuid, file_uuid = _uuid(task_id), _uuid(file_id)
        if not task_uuid or not file_uuid:
            return None
        with self.session_factory() as session, session.begin():
            file_item = session.scalar(
                select(TaskFile)
                .where(TaskFile.id == file_uuid, TaskFile.task_id == task_uuid)
                .with_for_update()
            )
            if not file_item or file_item.status in {"deleted", "unavailable"}:
                return None
            active_jobs = session.scalars(
                select(QueueJob).where(
                    QueueJob.job_type == "document_parse",
                    QueueJob.status.in_({"queued", "pending", "running"}),
                )
            )
            active = next(
                (
                    job
                    for job in active_jobs
                    if (job.payload or {}).get("file_id") == file_id
                    and (job.payload or {}).get("storage_key") == file_item.storage_key
                ),
                None,
            )
            if active:
                result = self._queue_job_dict(active)
                result["reused"] = True
                return result
            job = QueueJob(
                job_code=f"PARSE-{uuid4().hex}",
                job_type="document_parse",
                queue_name="document_parse",
                status="queued",
                payload={
                    "task_id": task_id,
                    "file_id": file_id,
                    "storage_key": file_item.storage_key,
                    "file_name": file_item.original_name,
                    "file_type": file_item.file_type,
                    "version_no": file_item.version_no,
                    "page_assets": (file_item.parse_summary or {}).get("page_assets", []),
                    "operator_user_id": payload.get("_operator_user_id"),
                    "trace_id": payload.get("_trace_id"),
                },
            )
            file_item.status = "parse_pending"
            session.add(job)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="task_file",
                entity_id=file_item.id,
                action_code="task_file.parse.queue",
                after_snapshot={"job_id": str(job.id), "status": file_item.status},
                trace_id=payload.get("_trace_id"),
            )
            return self._queue_job_dict(job)

    def start_task_file_parse(
        self, file_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        file_uuid = _uuid(file_id)
        if not file_uuid:
            return None
        context = payload or {}
        with self.session_factory() as session, session.begin():
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.status in {"deleted", "unavailable"}:
                return None
            if context.get("_storage_key") and context["_storage_key"] != file_item.storage_key:
                return None
            file_item.status = "parsing"
            session.flush()
            return self._file_dict(file_item)

    def complete_task_file_parse(
        self,
        file_id: str,
        blocks: list[dict[str, Any]],
        summary: dict[str, Any],
        payload: dict[str, Any] | None = None,
        page_assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        file_uuid = _uuid(file_id)
        if not file_uuid:
            return None
        context = payload or {}
        with self.session_factory() as session, session.begin():
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.status in {"deleted", "unavailable"}:
                return None
            if context.get("_storage_key") and context["_storage_key"] != file_item.storage_key:
                return None
            for old_block in session.scalars(select(ParsedBlock).where(ParsedBlock.file_id == file_uuid)):
                session.delete(old_block)
            session.add_all(
                [
                    ParsedBlock(
                        file_id=file_uuid,
                        page_no=block["page_no"],
                        block_type=block["block_type"],
                        content_text=block.get("content_text"),
                        bbox=block.get("bbox"),
                        confidence=block.get("confidence", 1.0),
                        source_ref=block.get("source_ref"),
                    )
                    for block in blocks
                ]
            )
            retry_count = (file_item.parse_summary or {}).get("retry_count", 0)
            quality_metrics = evaluate_parse_quality(blocks, summary)
            file_item.parse_summary = {
                **summary,
                "quality_metrics": quality_metrics,
                "quality_review": {"status": "pending" if quality_metrics["review_required"] else "not_required"},
                "page_assets": page_assets or [],
                "retry_count": retry_count,
                "parsed_at": datetime.now(UTC).isoformat(),
            }
            file_item.status = "parsed"
            self._log(
                session,
                operator_user_id=context.get("_operator_user_id"),
                entity_type="task_file",
                entity_id=file_item.id,
                action_code="task_file.parse.complete",
                after_snapshot={"status": "parsed", "parse_summary": file_item.parse_summary},
                trace_id=context.get("_trace_id"),
            )
            session.flush()
            return self._file_dict(file_item)

    def fail_task_file_parse(
        self,
        file_id: str,
        error: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        file_uuid = _uuid(file_id)
        if not file_uuid:
            return None
        context = payload or {}
        with self.session_factory() as session, session.begin():
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.status in {"deleted", "unavailable"}:
                return None
            if context.get("_storage_key") and context["_storage_key"] != file_item.storage_key:
                return None
            file_item.parse_summary = {
                **(file_item.parse_summary or {}),
                "error": error,
                "failed_at": datetime.now(UTC).isoformat(),
            }
            file_item.status = "parse_failed"
            self._log(
                session,
                operator_user_id=context.get("_operator_user_id"),
                entity_type="task_file",
                entity_id=file_item.id,
                action_code="task_file.parse.fail",
                after_snapshot={"status": "parse_failed", "error": error},
                trace_id=context.get("_trace_id"),
            )
            session.flush()
            return self._file_dict(file_item)

    @staticmethod
    def _parsed_block_dict(item: ParsedBlock) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "file_id": str(item.file_id),
            "page_no": item.page_no,
            "block_type": item.block_type,
            "content_text": item.content_text,
            "bbox": item.bbox,
            "confidence": float(item.confidence),
            "source_ref": item.source_ref,
            "created_at": _iso(item.created_at),
        }

    def list_task_file_blocks(self, task_id: str, file_id: str) -> list[dict[str, Any]] | None:
        task_uuid, file_uuid = _uuid(task_id), _uuid(file_id)
        if not task_uuid or not file_uuid:
            return None
        with self.session_factory() as session:
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.task_id != task_uuid or file_item.status == "deleted":
                return None
            blocks = session.scalars(
                select(ParsedBlock)
                .where(ParsedBlock.file_id == file_uuid)
                .order_by(ParsedBlock.page_no, ParsedBlock.created_at)
            )
            return [self._parsed_block_dict(item) for item in blocks]

    def update_task_file_block(self, task_id: str, file_id: str, block_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task_uuid, file_uuid, block_uuid = _uuid(task_id), _uuid(file_id), _uuid(block_id)
        if not task_uuid or not file_uuid or not block_uuid:
            return None
        with self.session_factory() as session, session.begin():
            file_item = session.get(TaskFile, file_uuid)
            block = session.get(ParsedBlock, block_uuid)
            if not file_item or file_item.task_id != task_uuid or not block or block.file_id != file_uuid or file_item.status != "parsed":
                return None
            before = self._parsed_block_dict(block)
            for key in ("content_text", "block_type", "bbox"):
                if key in payload:
                    setattr(block, key, payload[key])
            block.confidence = 1.0
            blocks = session.scalars(select(ParsedBlock).where(ParsedBlock.file_id == file_uuid)).all()
            block_payloads = [self._parsed_block_dict(item) for item in blocks]
            summary = dict(file_item.parse_summary or {})
            summary["quality_metrics"] = evaluate_parse_quality(block_payloads, summary)
            summary["manual_revision_count"] = int(summary.get("manual_revision_count", 0)) + 1
            summary["quality_review"] = {"status": "pending", "reason": "block_revised"}
            file_item.parse_summary = summary
            session.flush()
            after = self._parsed_block_dict(block)
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="parsed_block", entity_id=block.id, action_code="parsed_block.manual_revision", before_snapshot=before, after_snapshot=after, reason=payload.get("reason"), trace_id=payload.get("_trace_id"))
            return after

    def review_task_file_parse(self, task_id: str, file_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task_uuid, file_uuid = _uuid(task_id), _uuid(file_id)
        if not task_uuid or not file_uuid:
            return None
        with self.session_factory() as session, session.begin():
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.task_id != task_uuid or file_item.status != "parsed":
                return None
            summary = dict(file_item.parse_summary or {})
            summary["quality_review"] = {"status": payload["decision"], "reason": payload.get("reason"), "reviewed_at": datetime.now(UTC).isoformat(), "reviewer_user_id": payload.get("_operator_user_id")}
            file_item.parse_summary = summary
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="task_file", entity_id=file_item.id, action_code=f"task_file.parse_review.{payload['decision']}", after_snapshot={"quality_review": summary["quality_review"]}, reason=payload.get("reason"), trace_id=payload.get("_trace_id"))
            session.flush()
            return self._file_dict(file_item)

    def list_task_file_pages(self, task_id: str, file_id: str) -> list[dict[str, Any]] | None:
        task_uuid, file_uuid = _uuid(task_id), _uuid(file_id)
        if not task_uuid or not file_uuid:
            return None
        with self.session_factory() as session:
            file_item = session.get(TaskFile, file_uuid)
            if not file_item or file_item.task_id != task_uuid or file_item.status == "deleted":
                return None
            return list((file_item.parse_summary or {}).get("page_assets", []))

    def _mutate_task_file(self, task_id: str, file_id: str, payload: dict[str, Any], action_code: str) -> dict[str, Any] | None:
        task_uuid, file_uuid = _uuid(task_id), _uuid(file_id)
        if not task_uuid or not file_uuid:
            return None
        with self.session_factory() as session, session.begin():
            task = session.get(AuditTask, task_uuid)
            item = session.get(TaskFile, file_uuid)
            if not task or not item or item.task_id != task.id or item.status == "deleted":
                return None
            before = self._file_dict(item)
            if action_code == "task_file.update":
                for key, attr in (("file_type", "file_type"), ("is_required", "is_required"), ("is_applicable", "is_applicable")):
                    if key in payload and payload[key] is not None:
                        setattr(item, attr, payload[key])
            elif action_code == "task_file.replace":
                duplicate = session.scalar(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.sha256 == payload["sha256"], TaskFile.id != item.id, TaskFile.status != "deleted"))
                if duplicate:
                    raise ValueError("file with the same content already exists")
                item.original_name = payload["file_name"]
                item.file_type = payload.get("file_type") or "other"
                item.mime_type = payload.get("content_type")
                item.file_size = payload["file_size"]
                item.sha256 = payload["sha256"]
                item.storage_key = payload["storage_key"]
                item.version_no += 1
                item.status = "uploaded"
                item.parse_summary = {}
                for block in session.scalars(select(ParsedBlock).where(ParsedBlock.file_id == item.id)):
                    session.delete(block)
                self._cancel_pending_file_parse_jobs(session, item.id)
            elif action_code == "task_file.delete":
                item.status = "deleted"
                self._cancel_pending_file_parse_jobs(session, item.id)
            else:
                summary = dict(item.parse_summary or {})
                summary["retry_count"] = int(summary.get("retry_count", 0)) + 1
                item.parse_summary = summary
                item.status = "parse_pending"
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="task_file", entity_id=item.id, action_code=action_code, before_snapshot=before, after_snapshot=self._file_dict(item), trace_id=payload.get("_trace_id"))
            session.flush()
            return self._file_dict(item)

    @staticmethod
    def _cancel_pending_file_parse_jobs(session: Session, file_id: UUID) -> None:
        jobs = session.scalars(
            select(QueueJob).where(
                QueueJob.job_type == "document_parse",
                QueueJob.status.in_({"queued", "pending"}),
            )
        )
        for job in jobs:
            if (job.payload or {}).get("file_id") == str(file_id):
                job.status = "canceled"
                job.finished_at = datetime.now(UTC)

    def create_round(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task_uuid = _uuid(task_id)
        if not task_uuid:
            return None
        with self.session_factory() as session, session.begin():
            task = session.get(AuditTask, task_uuid)
            if not task:
                return None
            previous_round = session.get(AuditRound, task.current_round_id) if task.current_round_id else None
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
            if payload.get("inherit_previous_snapshot", True) and previous_round:
                inherited = session.scalars(
                    select(RoundRule).where(RoundRule.round_id == previous_round.id).order_by(RoundRule.created_at)
                ).all()
                snapshot_no = f"RULE-SNAPSHOT-R{round_item.round_no}-{str(round_item.id)[:8]}"
                for previous_rule in inherited:
                    session.add(
                        RoundRule(
                            round_id=round_item.id,
                            rule_version_id=previous_rule.rule_version_id,
                            executor_version_id=previous_rule.executor_version_id,
                            source_type=previous_rule.source_type,
                            enabled=previous_rule.enabled,
                            override_payload=previous_rule.override_payload,
                            disable_reason=previous_rule.disable_reason,
                            snapshot_no=snapshot_no,
                        )
                    )
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
            model_config = session.scalar(
                select(ModelConfig)
                .where(ModelConfig.model_kind == "text", ModelConfig.status == "active")
                .order_by(ModelConfig.created_at.desc())
                .limit(1)
            )
            if model_config:
                audit_run.model_snapshot_id = model_config.id
                round_item.model_snapshot_id = model_config.id
            round_rules = session.scalars(
                select(RoundRule).where(RoundRule.round_id == round_item.id, RoundRule.enabled.is_(True))
            ).all()
            for round_rule in round_rules:
                input_snapshot = {
                    "round_id": str(round_item.id),
                    "rule_snapshot_no": round_rule.snapshot_no,
                    "rule_version_id": str(round_rule.rule_version_id),
                }
                normalized_input_hash = sha256(
                    json.dumps(input_snapshot, sort_keys=True).encode()
                ).hexdigest()
                session.add(
                    RuleExecution(
                        audit_run_id=audit_run.id,
                        round_id=round_item.id,
                        rule_version_id=round_rule.rule_version_id,
                        executor_version_id=round_rule.executor_version_id,
                        status="pending",
                        input_snapshot=input_snapshot,
                        normalized_input_hash=normalized_input_hash,
                    )
                )
            dynamic_definition = session.scalar(
                select(RuleDefinition).where(RuleDefinition.rule_code == "DYNAMIC_STANDARD_CLAUSE_REVIEW")
            )
            dynamic_version = session.scalar(
                select(RuleVersion)
                .where(
                    RuleVersion.rule_definition_id == dynamic_definition.id,
                    RuleVersion.status == "published",
                )
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            ) if dynamic_definition else None
            dynamic_items = session.scalars(
                select(DynamicAuditItem).where(
                    DynamicAuditItem.round_id == round_item.id,
                    DynamicAuditItem.applicability_status == "applicable",
                )
            ).all()
            for dynamic_item in dynamic_items:
                if not dynamic_version:
                    continue
                customer_evidence: list[dict[str, Any]] = []
                task_files = session.scalars(
                    select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.status == "parsed")
                ).all()
                for file_item in task_files:
                    parse_summary = file_item.parse_summary or {}
                    review_status = (parse_summary.get("quality_review") or {}).get("status")
                    if not review_status and (parse_summary.get("quality_metrics") or {}).get("review_required") is False:
                        review_status = "not_required"
                    if review_status not in {"accepted", "not_required"}:
                        continue
                    blocks = session.scalars(
                        select(ParsedBlock).where(ParsedBlock.file_id == file_item.id).order_by(ParsedBlock.page_no).limit(50)
                    ).all()
                    customer_evidence.extend(
                        {"file_id": str(file_item.id), "page_no": block.page_no, "bbox": block.bbox, "excerpt_text": block.content_text, "confidence": float(block.confidence), "source_ref": block.source_ref}
                        for block in blocks if block.content_text
                    )
                clause = session.get(StandardClause, dynamic_item.source_clause_id) if dynamic_item.source_clause_id else None
                standard_evidence = [{"clause_id": str(clause.id), "clause_code": clause.clause_code, "excerpt_text": clause.original_text, "confidence": float(clause.confidence)}] if clause else []
                provider = session.get(ModelProvider, model_config.provider_id) if model_config else None
                model_snapshot = {"config_id": str(model_config.id), "provider_code": provider.provider_code if provider else None, "model_code": model_config.model_code, "credential_version": model_config.credential_version} if model_config else {}
                dynamic_item.execution_mode = "ai"
                dynamic_item.customer_evidence = {"items": customer_evidence, "count": len(customer_evidence)}
                dynamic_item.standard_evidence = {"items": standard_evidence, "count": len(standard_evidence)}
                input_snapshot = {"round_id": str(round_item.id), "dynamic_item": {"id": str(dynamic_item.id), "subject_code": dynamic_item.subject_code, "subject_name": dynamic_item.subject_name}, "evidence": customer_evidence, "standard_evidence": standard_evidence, "model_snapshot": model_snapshot, "trace_id": payload.get("_trace_id")}
                session.add(
                    RuleExecution(
                        audit_run_id=audit_run.id, round_id=round_item.id,
                        rule_version_id=dynamic_version.id, dynamic_item_id=dynamic_item.id,
                        executor_version_id=dynamic_version.executor_version_id, status="pending",
                        input_snapshot=input_snapshot,
                        normalized_input_hash=sha256(json.dumps(input_snapshot, sort_keys=True).encode()).hexdigest(),
                    )
                )
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

    def local_rerun(self, round_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        affected_codes = set(payload.get("affected_rule_codes") or [])
        with self.session_factory() as session, session.begin():
            round_item = session.get(AuditRound, round_uuid)
            if not round_item:
                return None
            round_rules = session.scalars(
                select(RoundRule).where(RoundRule.round_id == round_item.id, RoundRule.enabled.is_(True))
            ).all()
            selected: list[RoundRule] = []
            selected_codes: set[str] = set()
            for round_rule in round_rules:
                version = session.get(RuleVersion, round_rule.rule_version_id)
                definition = session.get(RuleDefinition, version.rule_definition_id) if version else None
                if definition and definition.rule_code in affected_codes:
                    selected.append(round_rule)
                    selected_codes.add(definition.rule_code)
            missing_codes = affected_codes - selected_codes
            if missing_codes:
                raise ValueError(f"rule is not present in round snapshot: {', '.join(sorted(missing_codes))}")
            if not selected:
                raise ValueError("no affected rule found in round snapshot")
            selected_version_ids = [item.rule_version_id for item in selected]
            for previous in session.scalars(
                select(RuleExecution).where(
                    RuleExecution.round_id == round_item.id,
                    RuleExecution.rule_version_id.in_(selected_version_ids),
                )
            ):
                previous.is_expired = True
            impact = RuleImpactAnalysis(
                round_id=round_item.id,
                trigger_type="local_rerun",
                trigger_payload={"reason": payload["reason"], "input_change": payload.get("input_change", {})},
                affected_rule_codes=sorted(affected_codes),
                estimated_rerun_scope={"rule_count": len(selected)},
                status="queued",
            )
            session.add(impact)
            session.flush()
            latest_run_no = session.scalar(
                select(func.max(AuditRun.run_no)).where(AuditRun.round_id == round_item.id)
            ) or 0
            audit_run = AuditRun(
                round_id=round_item.id,
                run_no=latest_run_no + 1,
                status="queued",
                summary={"run_scope": "local", "impact_analysis_id": str(impact.id)},
            )
            session.add(audit_run)
            session.flush()
            for round_rule in selected:
                input_snapshot = {
                    "round_id": str(round_item.id), "rule_snapshot_no": round_rule.snapshot_no,
                    "rule_version_id": str(round_rule.rule_version_id),
                    "input_change": payload.get("input_change", {}),
                }
                session.add(
                    RuleExecution(
                        audit_run_id=audit_run.id, round_id=round_item.id,
                        rule_version_id=round_rule.rule_version_id,
                        executor_version_id=round_rule.executor_version_id, status="pending",
                        input_snapshot=input_snapshot,
                        normalized_input_hash=sha256(json.dumps(input_snapshot, sort_keys=True).encode()).hexdigest(),
                    )
                )
            job = QueueJob(
                job_code=f"LOCAL-RERUN-{audit_run.id}", job_type="local_rerun", queue_name="audit",
                payload={"audit_run_id": str(audit_run.id), "round_id": str(round_item.id),
                         "impact_analysis_id": str(impact.id)},
                status="queued",
            )
            session.add(job)
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"), entity_type="rule_impact_analysis",
                entity_id=impact.id, action_code="audit.local_rerun",
                after_snapshot={"affected_rule_codes": sorted(affected_codes), "audit_run_id": str(audit_run.id)},
                reason=payload["reason"], trace_id=payload.get("_trace_id"),
            )
            session.flush()
            result = self._audit_run_dict(audit_run, job_id=job.id)
            result.update({"impact_analysis_id": str(impact.id), "run_scope": "local", "affected_rule_codes": sorted(affected_codes)})
            return result

    def list_audit_runs(self, round_id: str) -> list[dict[str, Any]] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            runs = session.scalars(
                select(AuditRun).where(AuditRun.round_id == round_uuid).order_by(AuditRun.run_no.desc())
            ).all()
            return [self._audit_run_dict(item) for item in runs]

    def get_audit_progress(self, round_id: str) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            latest = session.scalar(
                select(AuditRun).where(AuditRun.round_id == round_uuid).order_by(AuditRun.run_no.desc()).limit(1)
            )
            executions = session.scalars(
                select(RuleExecution).where(RuleExecution.audit_run_id == latest.id)
            ).all() if latest else []
            counts: dict[str, int] = {}
            for item in executions:
                counts[item.status] = counts.get(item.status, 0) + 1
            terminal = {"succeeded", "failed", "unable_to_determine", "exception", "canceled", "expired"}
            completed = sum(count for status, count in counts.items() if status in terminal)
            total = len(executions)
            return {
                "round_id": round_id, "audit_run_id": str(latest.id) if latest else None,
                "run_no": latest.run_no if latest else None, "status": latest.status if latest else "not_started",
                "total": total, "completed": completed,
                "progress_percent": round(completed * 100 / total, 1) if total else 0.0,
                "status_counts": counts,
            }

    def check_round_publishability(self, round_id: str) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            blockers = []
            confirmed_count = session.scalar(
                select(func.count(RoundStandard.id)).where(
                    RoundStandard.round_id == round_uuid, RoundStandard.snapshot_no.like("SNAPSHOT-%")
                )
            ) or 0
            if not confirmed_count:
                blockers.append({"code": "NO_CONFIRMED_STANDARD", "message": "本轮尚未确认适用标准"})
            pending_coverage = session.scalar(
                select(func.count(RoundStandardCoverage.id)).where(
                    RoundStandardCoverage.round_id == round_uuid,
                    RoundStandardCoverage.coverage_status == "to_confirm",
                )
            ) or 0
            if pending_coverage:
                blockers.append({"code": "COVERAGE_TO_CONFIRM", "message": "标准覆盖清单仍有待确认项", "count": pending_coverage})
            executions = session.scalars(
                select(RuleExecution).where(RuleExecution.round_id == round_uuid, RuleExecution.is_expired.is_(False))
            ).all()
            if not executions:
                blockers.append({"code": "NO_EXECUTION", "message": "本轮尚未创建规则执行记录"})
            pending_count = sum(item.status in {"pending", "running"} for item in executions)
            exception_count = sum(item.status in {"exception", "expired"} for item in executions)
            if pending_count:
                blockers.append({"code": "EXECUTION_INCOMPLETE", "message": "仍有规则执行未完成", "count": pending_count})
            if exception_count:
                blockers.append({"code": "EXECUTION_EXCEPTION", "message": "仍有执行异常未处置", "count": exception_count})
            open_issue_count = session.scalar(
                select(func.count(AuditIssue.id)).where(
                    AuditIssue.round_id == round_uuid, AuditIssue.status == "open"
                )
            ) or 0
            if open_issue_count:
                blockers.append({"code": "ISSUE_TO_REVIEW", "message": "仍有问题等待人工复核", "count": open_issue_count})
            return {"round_id": round_id, "can_publish": not blockers, "blockers": blockers}

    def _rule_execution_dict(self, session: Session, item: RuleExecution) -> dict[str, Any]:
        version = session.get(RuleVersion, item.rule_version_id)
        definition = session.get(RuleDefinition, version.rule_definition_id) if version else None
        return {
            "id": str(item.id),
            "audit_run_id": str(item.audit_run_id),
            "round_id": str(item.round_id),
            "rule_version_id": str(item.rule_version_id),
            "rule_code": definition.rule_code if definition else None,
            "executor_version_id": str(item.executor_version_id),
            "dynamic_item_id": str(item.dynamic_item_id) if item.dynamic_item_id else None,
            "status": item.status,
            "input_snapshot": item.input_snapshot,
            "normalized_input_hash": item.normalized_input_hash,
            "result_payload": item.result_payload,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "retry_count": item.retry_count,
            "attempt_count": item.attempt_count,
            "elapsed_ms": item.elapsed_ms,
            "is_expired": item.is_expired,
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        }

    def list_rule_executions(self, round_id: str) -> list[dict[str, Any]] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            items = session.scalars(
                select(RuleExecution).where(RuleExecution.round_id == round_uuid).order_by(RuleExecution.created_at)
            ).all()
            return [self._rule_execution_dict(session, item) for item in items]

    def get_rule_execution(self, execution_id: str) -> dict[str, Any] | None:
        execution_uuid = _uuid(execution_id)
        if not execution_uuid:
            return None
        with self.session_factory() as session:
            item = session.get(RuleExecution, execution_uuid)
            return self._rule_execution_dict(session, item) if item else None

    def list_execution_attempts(self, execution_id: str) -> list[dict[str, Any]] | None:
        execution_uuid = _uuid(execution_id)
        if not execution_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(RuleExecution, execution_uuid):
                return None
            attempts = session.scalars(
                select(RuleExecutionAttempt)
                .where(RuleExecutionAttempt.rule_execution_id == execution_uuid)
                .order_by(RuleExecutionAttempt.attempt_no)
            ).all()
            return [{
                "id": str(item.id), "rule_execution_id": str(item.rule_execution_id),
                "attempt_no": item.attempt_no, "attempt_kind": item.attempt_kind,
                "executor_version_id": str(item.executor_version_id), "status": item.status,
                "input_payload": item.input_payload, "output_payload": item.output_payload,
                "error_payload": item.error_payload, "token_usage": item.token_usage,
                "started_at": _iso(item.started_at), "finished_at": _iso(item.finished_at),
            } for item in attempts]

    def _issue_dict(self, session: Session, issue: AuditIssue) -> dict[str, Any]:
        sources = session.scalars(
            select(IssueSource).where(IssueSource.issue_id == issue.id).order_by(IssueSource.created_at)
        ).all()
        evidence = session.scalars(
            select(IssueEvidence).where(IssueEvidence.issue_id == issue.id).order_by(IssueEvidence.created_at)
        ).all()
        return {
            "id": str(issue.id), "round_id": str(issue.round_id), "issue_code": issue.issue_code,
            "title": issue.title, "description": issue.description, "category_code": issue.category_code,
            "severity": issue.severity, "status": issue.status, "system_conclusion": issue.system_conclusion,
            "manual_conclusion": issue.manual_conclusion, "affects_conclusion": issue.affects_conclusion,
            "manual_reason": issue.manual_reason, "created_at": _iso(issue.created_at),
            "updated_at": _iso(issue.updated_at),
            "sources": [{
                "id": str(item.id), "source_type": item.source_type,
                "rule_execution_id": str(item.rule_execution_id) if item.rule_execution_id else None,
                "dynamic_item_id": str(item.dynamic_item_id) if item.dynamic_item_id else None,
                "source_status": item.source_status, "source_payload": item.source_payload,
            } for item in sources],
            "evidence": [{
                "id": str(item.id), "evidence_type": item.evidence_type,
                "file_id": str(item.file_id) if item.file_id else None,
                "clause_id": str(item.clause_id) if item.clause_id else None,
                "page_no": item.page_no, "bbox": item.bbox, "excerpt_text": item.excerpt_text,
                "artifact_uri": item.artifact_uri,
                "confidence": float(item.confidence) if item.confidence is not None else None,
            } for item in evidence],
        }

    def list_issues(self, round_id: str | None = None) -> list[dict[str, Any]]:
        round_uuid = _uuid(round_id) if round_id else None
        with self.session_factory() as session:
            query = select(AuditIssue).order_by(AuditIssue.created_at.desc())
            if round_uuid:
                query = query.where(AuditIssue.round_id == round_uuid)
            return [self._issue_dict(session, item) for item in session.scalars(query)]

    def create_manual_issue(self, round_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session, session.begin():
            if not session.get(AuditRound, round_uuid):
                return None
            count = session.scalar(select(func.count(AuditIssue.id)).where(AuditIssue.round_id == round_uuid)) or 0
            issue = AuditIssue(round_id=round_uuid, issue_code=f"MANUAL-{count + 1:04d}", title=payload["title"], description=payload["description"], category_code=payload["category_code"], severity=payload.get("severity", "一般"), status="open", system_conclusion="failed", affects_conclusion=payload.get("affects_conclusion", False))
            session.add(issue)
            session.flush()
            session.add(IssueSource(issue_id=issue.id, source_type="manual", source_status="confirmed", source_payload={"operator_user_id": payload.get("_operator_user_id")}))
            for evidence in payload.get("evidence", []):
                session.add(IssueEvidence(issue_id=issue.id, evidence_type=evidence.get("evidence_type", "customer"), file_id=_uuid(evidence.get("file_id")), clause_id=_uuid(evidence.get("clause_id")), page_no=evidence.get("page_no"), bbox=evidence.get("bbox"), excerpt_text=evidence.get("excerpt_text"), artifact_uri=evidence.get("artifact_uri"), confidence=evidence.get("confidence")))
            self._log(session, operator_user_id=payload.get("_operator_user_id"), entity_type="audit_issue", entity_id=issue.id, action_code="issue.manual_create", after_snapshot=self._issue_dict(session, issue), trace_id=payload.get("_trace_id"))
            session.flush()
            return self._issue_dict(session, issue)

    @staticmethod
    def _report_dict(report: Report) -> dict[str, Any]:
        return {
            "id": str(report.id), "round_id": str(report.round_id), "report_no": report.report_no,
            "report_type": report.report_type, "version_no": report.version_no,
            "conclusion": report.conclusion, "status": report.status,
            "content_snapshot": report.content_snapshot or {},
            "word_object_key": report.word_object_key, "pdf_object_key": report.pdf_object_key,
            "published_at": _iso(report.published_at), "created_at": _iso(report.created_at),
            "updated_at": _iso(report.updated_at),
        }

    def list_reports(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            items = session.scalars(select(Report).order_by(Report.created_at.desc())).all()
            return [self._report_dict(item) for item in items]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        report_uuid = _uuid(report_id)
        if not report_uuid:
            return None
        with self.session_factory() as session:
            report = session.get(Report, report_uuid)
            return self._report_dict(report) if report else None

    def create_report(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        round_uuid = _uuid(payload.get("round_id"))
        if not round_uuid:
            return None
        with self.session_factory() as session, session.begin():
            round_item = session.get(AuditRound, round_uuid)
            task = session.get(AuditTask, round_item.task_id) if round_item else None
            if not round_item or not task:
                return None
            version_no = (session.scalar(
                select(func.max(Report.version_no)).where(Report.round_id == round_item.id)
            ) or 0) + 1
            issues = [self._issue_dict(session, item) for item in session.scalars(select(AuditIssue).where(AuditIssue.round_id == round_item.id).order_by(AuditIssue.created_at))]
            executions = list(session.scalars(select(RuleExecution).where(RuleExecution.round_id == round_item.id, RuleExecution.is_expired.is_(False))))
            report_type = payload.get("report_type", "formal")
            standards = [self._round_standard_dict(session, item) for item in session.scalars(select(RoundStandard).where(RoundStandard.round_id == round_item.id).order_by(RoundStandard.created_at))]
            statuses = sorted({item.status for item in executions})
            content_snapshot = {
                "title": "审核意见单" if report_type == "opinion" else "煤矿安标技术文档审核报告",
                "task": {"task_no": task.task_no, "customer_name": task.customer_name, "product_name": task.product_name, "product_model": task.product_model},
                "round": {"round_no": round_item.round_no, "round_note": round_item.round_note},
                "standards": standards,
                "execution_summary": {"total": len(executions), "status_counts": {status: sum(1 for item in executions if item.status == status) for status in statuses}},
                "issue_summary": {"total": len(issues), "confirmed": sum(1 for item in issues if item.get("status") == "confirmed")},
                "issues": issues,
                "conclusion": payload.get("conclusion", "through"),
            }
            report = Report(
                round_id=round_item.id, report_no=f"{task.task_no}-REP-V{version_no}",
                report_type=report_type, version_no=version_no,
                conclusion=payload.get("conclusion", "through"), content_snapshot=content_snapshot, status="draft",
            )
            session.add(report)
            session.flush()
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"), entity_type="report",
                entity_id=report.id, action_code="report.create",
                after_snapshot={"report_no": report.report_no, "round_id": str(round_item.id)},
                trace_id=payload.get("_trace_id"),
            )
            return self._report_dict(report)

    def publish_report(self, report_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        report_snapshot = self.get_report(report_id)
        if not report_snapshot:
            return None
        check = self.check_round_publishability(report_snapshot["round_id"])
        if check and not check["can_publish"]:
            raise ValueError({"message": "round is not publishable", "blockers": check["blockers"]})
        report_uuid = _uuid(report_id)
        with self.session_factory() as session, session.begin():
            report = session.get(Report, report_uuid)
            if not report:
                return None
            round_item = session.get(AuditRound, report.round_id)
            task = session.get(AuditTask, round_item.task_id) if round_item else None
            report.status = "published"
            report.published_at = datetime.now(UTC)
            report.word_object_key = f"reports/{report.report_no}.docx"
            report.pdf_object_key = f"reports/{report.report_no}.pdf"
            if round_item:
                round_item.status = "completed"
            if task:
                task.status = "completed"
                task.final_conclusion = report.conclusion
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"), entity_type="report",
                entity_id=report.id, action_code="report.publish",
                after_snapshot={"status": "published", "conclusion": report.conclusion},
                reason=payload.get("reason"), trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._report_dict(report)

    def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        issue_uuid = _uuid(issue_id)
        if not issue_uuid:
            return None
        with self.session_factory() as session:
            issue = session.get(AuditIssue, issue_uuid)
            return self._issue_dict(session, issue) if issue else None

    def update_issue(self, issue_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        issue_uuid = _uuid(issue_id)
        if not issue_uuid:
            return None
        with self.session_factory() as session, session.begin():
            issue = session.get(AuditIssue, issue_uuid)
            if not issue:
                return None
            before = self._issue_dict(session, issue)
            for key in ("title", "description", "category_code", "severity", "affects_conclusion", "manual_conclusion"):
                if payload.get(key) is not None:
                    setattr(issue, key, payload[key])
            if payload.get("reason"):
                issue.manual_reason = payload["reason"]
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"),
                entity_type="audit_issue",
                entity_id=issue.id,
                action_code="issue.update",
                before_snapshot=before,
                after_snapshot=self._issue_dict(session, issue),
                reason=payload.get("reason"),
                trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return self._issue_dict(session, issue)

    def set_issue_status(
        self,
        issue_id: str,
        status: str,
        reason: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"open", "confirmed", "rejected", "closed"}:
            raise ValueError("invalid issue status")
        issue_uuid = _uuid(issue_id)
        if not issue_uuid:
            return None
        with self.session_factory() as session, session.begin():
            issue = session.get(AuditIssue, issue_uuid)
            if not issue:
                return None
            before_status = issue.status
            issue.status = status
            if reason:
                issue.manual_reason = reason
            operation_context = context or {}
            self._log(
                session,
                operator_user_id=operation_context.get("_operator_user_id"),
                entity_type="audit_issue",
                entity_id=issue.id,
                action_code=f"issue.{status}",
                before_snapshot={"status": before_status},
                after_snapshot={"status": status},
                reason=reason,
                trace_id=operation_context.get("_trace_id"),
            )
            session.flush()
            return self._issue_dict(session, issue)

    def record_execution_attempt(self, execution_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        execution_uuid = _uuid(execution_id)
        if not execution_uuid:
            return None
        allowed_statuses = {"running", "succeeded", "failed", "unable_to_determine", "exception", "canceled", "expired"}
        status = payload.get("status", "succeeded")
        if status not in allowed_statuses:
            raise ValueError("invalid execution attempt status")
        with self.session_factory() as session, session.begin():
            execution = session.get(RuleExecution, execution_uuid)
            if not execution:
                return None
            attempt_no = (session.scalar(
                select(func.max(RuleExecutionAttempt.attempt_no)).where(
                    RuleExecutionAttempt.rule_execution_id == execution.id
                )
            ) or 0) + 1
            attempt = RuleExecutionAttempt(
                rule_execution_id=execution.id,
                attempt_no=attempt_no,
                attempt_kind=payload.get("attempt_kind", "normal"),
                executor_version_id=execution.executor_version_id,
                input_payload=payload.get("input_payload") or execution.input_snapshot,
                output_payload=payload.get("output_payload"),
                error_payload=payload.get("error_payload"),
                model_version_id=_uuid(payload.get("model_version_id")),
                token_usage=payload.get("token_usage") or {},
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC) if status != "running" else None,
                status=status,
            )
            session.add(attempt)
            execution.status = status
            execution.result_payload = payload.get("output_payload")
            execution.elapsed_ms = payload.get("elapsed_ms")
            execution.confidence = payload.get("confidence")
            execution.attempt_count = attempt_no
            if execution.dynamic_item_id:
                coverage = session.scalar(
                    select(RoundStandardCoverage).where(
                        RoundStandardCoverage.round_id == execution.round_id,
                        RoundStandardCoverage.dynamic_item_id == execution.dynamic_item_id,
                    )
                )
                if coverage:
                    warnings = (payload.get("output_payload") or {}).get("warnings") or []
                    coverage.coverage_status = {
                        "succeeded": "executed_passed",
                        "failed": "executed_failed",
                        "unable_to_determine": (
                            "missing_data" if "EVIDENCE_INSUFFICIENT" in warnings else "unable_to_determine"
                        ),
                        "exception": "execution_exception",
                    }.get(status, coverage.coverage_status)
                    coverage.reason = (payload.get("output_payload") or {}).get("description")
                    coverage.publish_check_status = "passed" if status == "succeeded" else "review_required"
            issue_payload = (payload.get("output_payload") or {}).get("issue")
            if issue_payload:
                issue_code = issue_payload.get("issue_code") or f"EXEC-{str(execution.id)[:8]}"
                issue = session.scalar(
                    select(AuditIssue).where(
                        AuditIssue.round_id == execution.round_id,
                        AuditIssue.issue_code == issue_code,
                    )
                )
                existing_issue = issue is not None
                incoming_severity = issue_payload.get("severity") or (issue.severity if issue else None)
                incoming_conclusion = issue_payload.get("system_conclusion") or "failed"
                is_conflict = bool(
                    issue
                    and (
                        issue.severity != incoming_severity
                        or issue.system_conclusion != incoming_conclusion
                    )
                )
                if not issue:
                    version = session.get(RuleVersion, execution.rule_version_id)
                    definition = session.get(RuleDefinition, version.rule_definition_id) if version else None
                    issue = AuditIssue(
                        round_id=execution.round_id,
                        issue_code=issue_code,
                        title=issue_payload.get("title") or (definition.rule_name if definition else "审核问题"),
                        description=issue_payload.get("description") or "规则执行发现不符合项",
                        category_code=issue_payload.get("category_code") or (
                            definition.default_issue_category if definition else "technical_compliance"
                        ),
                        severity=issue_payload.get("severity") or (
                            definition.default_severity if definition else "一般"
                        ),
                        status="open",
                        system_conclusion=issue_payload.get("system_conclusion") or "failed",
                        affects_conclusion=issue_payload.get("affects_conclusion", False),
                    )
                    session.add(issue)
                    session.flush()
                customer_evidence = issue_payload.get("customer_evidence") or []
                standard_evidence = issue_payload.get("standard_evidence") or []
                if isinstance(customer_evidence, dict):
                    customer_evidence = [customer_evidence]
                if isinstance(standard_evidence, dict):
                    standard_evidence = [standard_evidence]
                source_status = "active"
                if not customer_evidence or not standard_evidence:
                    source_status = "evidence_insufficient"
                    if issue.status == "open":
                        issue.system_conclusion = "unable_to_determine"
                if existing_issue and is_conflict:
                    source_status = "conflict"
                    issue.system_conclusion = "conflict_requires_review"
                    issue.status = "open"
                    for related in session.scalars(select(IssueSource).where(IssueSource.issue_id == issue.id)):
                        related.source_status = "conflict"
                source = session.scalar(
                    select(IssueSource).where(
                        IssueSource.issue_id == issue.id,
                        IssueSource.rule_execution_id == execution.id,
                    )
                )
                if not source:
                    source = IssueSource(
                        issue_id=issue.id,
                        source_type="rule_execution",
                        rule_execution_id=execution.id,
                        dynamic_item_id=execution.dynamic_item_id,
                        source_status=source_status,
                        source_payload=issue_payload,
                    )
                    session.add(source)
                    for evidence_type, entries in (("customer", customer_evidence), ("standard", standard_evidence)):
                        for entry in entries:
                            session.add(
                                IssueEvidence(
                                    issue_id=issue.id,
                                    evidence_type=evidence_type,
                                    file_id=_uuid(entry.get("file_id")),
                                    clause_id=_uuid(entry.get("clause_id")),
                                    page_no=entry.get("page_no"),
                                    bbox=entry.get("bbox"),
                                    excerpt_text=entry.get("excerpt_text"),
                                    artifact_uri=entry.get("artifact_uri"),
                                    confidence=entry.get("confidence"),
                                )
                            )
            session.flush()
            run_executions = session.scalars(
                select(RuleExecution).where(RuleExecution.audit_run_id == execution.audit_run_id)
            ).all()
            terminal = {"succeeded", "failed", "unable_to_determine", "exception", "canceled", "expired"}
            if run_executions and all(item.status in terminal for item in run_executions):
                audit_run = session.get(AuditRun, execution.audit_run_id)
                if audit_run and audit_run.status in {"queued", "running"}:
                    status_counts = {
                        status: sum(item.status == status for item in run_executions)
                        for status in sorted({item.status for item in run_executions})
                    }
                    audit_run.status = "completed"
                    audit_run.finished_at = datetime.now(UTC)
                    audit_run.summary = {"total": len(run_executions), "status_counts": status_counts}
                    round_item = session.get(AuditRound, execution.round_id)
                    task = session.get(AuditTask, round_item.task_id) if round_item else None
                    if round_item:
                        round_item.status = "awaiting_review"
                    if task:
                        task.status = "awaiting_review"
            return self._rule_execution_dict(session, execution)

    def retry_rule_execution(self, execution_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        execution_uuid = _uuid(execution_id)
        if not execution_uuid:
            return None
        with self.session_factory() as session, session.begin():
            execution = session.get(RuleExecution, execution_uuid)
            if not execution:
                return None
            if execution.status in {"running", "pending"}:
                raise ValueError("execution is already queued or running")
            execution.retry_count += 1
            execution.status = "pending"
            session.flush()
            return self._rule_execution_dict(session, execution)

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
            "run_scope": (audit_run.summary or {}).get("run_scope", "full"),
            "impact_analysis_id": (audit_run.summary or {}).get("impact_analysis_id"),
            "summary": audit_run.summary,
            "started_at": _iso(audit_run.started_at),
            "finished_at": _iso(audit_run.finished_at),
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
            clauses = session.scalars(
                select(StandardClause)
                .where(StandardClause.parse_revision_id == selected.parse_revision_id)
                .order_by(StandardClause.clause_code)
            ).all()
            for clause in clauses:
                dynamic_item = session.scalar(
                    select(DynamicAuditItem).where(
                        DynamicAuditItem.round_id == round_item.id,
                        DynamicAuditItem.source_clause_id == clause.id,
                    )
                )
                if not dynamic_item:
                    dynamic_item = DynamicAuditItem(
                        round_id=round_item.id,
                        source_clause_id=clause.id,
                        subject_code=clause.clause_code,
                        subject_name=clause.title or clause.clause_code,
                        applicability_status="to_confirm",
                        execution_mode="ai",
                        input_profile={"constraint_level": clause.constraint_level},
                    )
                    session.add(dynamic_item)
                    session.flush()
                coverage = session.scalar(
                    select(RoundStandardCoverage).where(
                        RoundStandardCoverage.round_id == round_item.id,
                        RoundStandardCoverage.standard_clause_id == clause.id,
                    )
                )
                if not coverage:
                    session.add(
                        RoundStandardCoverage(
                            round_id=round_item.id,
                            standard_clause_id=clause.id,
                            dynamic_item_id=dynamic_item.id,
                            coverage_status="to_confirm",
                            publish_check_status="pending",
                        )
                    )
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

    def list_dynamic_items(self, round_id: str) -> list[dict[str, Any]] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            items = session.scalars(
                select(DynamicAuditItem).where(DynamicAuditItem.round_id == round_uuid).order_by(DynamicAuditItem.created_at)
            ).all()
            return [
                {
                    "id": str(item.id), "round_id": str(item.round_id),
                    "source_clause_id": str(item.source_clause_id) if item.source_clause_id else None,
                    "subject_code": item.subject_code, "subject_name": item.subject_name,
                    "applicability_status": item.applicability_status, "execution_mode": item.execution_mode,
                    "customer_evidence": item.customer_evidence, "standard_evidence": item.standard_evidence,
                    "manual_state": item.manual_state,
                    "input_profile": item.input_profile,
                }
                for item in items
            ]

    def decide_dynamic_item(
        self, round_id: str, item_id: str, decision: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        if decision not in {"applicable", "not_applicable", "manual_review"}:
            raise ValueError("invalid dynamic item decision")
        round_uuid = _uuid(round_id)
        item_uuid = _uuid(item_id)
        if not round_uuid or not item_uuid:
            return None
        with self.session_factory() as session, session.begin():
            item = session.scalar(
                select(DynamicAuditItem).where(DynamicAuditItem.id == item_uuid, DynamicAuditItem.round_id == round_uuid)
            )
            if not item:
                return None
            item.applicability_status = decision
            item.manual_state = "confirmed" if decision != "manual_review" else "pending"
            coverage = session.scalar(
                select(RoundStandardCoverage).where(
                    RoundStandardCoverage.round_id == round_uuid,
                    RoundStandardCoverage.dynamic_item_id == item.id,
                )
            )
            if coverage:
                coverage.coverage_status = "to_confirm" if decision == "manual_review" else decision
                coverage.reason = payload.get("reason")
            self._log(
                session,
                operator_user_id=payload.get("_operator_user_id"), entity_type="dynamic_audit_item",
                entity_id=item.id, action_code=f"dynamic_item.{decision}",
                after_snapshot={"applicability_status": decision, "reason": payload.get("reason")},
                reason=payload.get("reason"), trace_id=payload.get("_trace_id"),
            )
            session.flush()
            return {
                "id": str(item.id), "round_id": str(item.round_id), "subject_code": item.subject_code,
                "subject_name": item.subject_name, "applicability_status": item.applicability_status,
                "execution_mode": item.execution_mode, "manual_state": item.manual_state,
                "manual_reason": payload.get("reason"),
            }

    def list_coverage(self, round_id: str) -> dict[str, Any] | None:
        round_uuid = _uuid(round_id)
        if not round_uuid:
            return None
        with self.session_factory() as session:
            if not session.get(AuditRound, round_uuid):
                return None
            items = session.scalars(
                select(RoundStandardCoverage).where(RoundStandardCoverage.round_id == round_uuid).order_by(RoundStandardCoverage.created_at)
            ).all()
            summary: dict[str, int] = {}
            for item in items:
                summary[item.coverage_status] = summary.get(item.coverage_status, 0) + 1
            return {"round_id": round_id, "summary": summary, "items": [{
                "id": str(item.id), "standard_clause_id": str(item.standard_clause_id),
                "dynamic_item_id": str(item.dynamic_item_id) if item.dynamic_item_id else None,
                "coverage_status": item.coverage_status, "reason": item.reason,
            } for item in items]}

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
