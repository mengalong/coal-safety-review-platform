from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.database import Base
from coal_platform.main import create_app
from coal_platform.models import (
    AuditRun,
    ExecutorDefinition,
    ExecutorVersion,
    OperationLog,
    QueueJob,
    RoundRule,
    RoundStandard,
    RuleDefinition,
    RulePack,
    RulePackItem,
    RuleVersion,
    Standard,
    StandardClause,
    StandardParseRevision,
    StandardVersion,
)
from coal_platform.sqlalchemy_store import SqlAlchemyStore
from coal_platform.storage import InMemoryObjectStorage
from coal_platform.store import DemoStore


def _store(database_path: Path) -> tuple[SqlAlchemyStore, sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = SqlAlchemyStore(factory)
    store.initialize(seed_demo_data=True)
    return store, factory


def test_database_store_persists_task_round_file_and_operation_logs(tmp_path: Path) -> None:
    database_path = tmp_path / "store.db"
    store, factory = _store(database_path)
    reviewer = store.authenticate("liming", DemoStore.demo_password)
    assert reviewer

    task = store.create_task(
        {
            "customer_name": "持久化测试企业",
            "product_name": "带式输送机",
            "product_model": "DSJ100/63/2x90",
            "owner_user_id": reviewer["id"],
            "_operator_user_id": reviewer["id"],
            "_trace_id": "persistence-test",
        }
    )
    second_round = store.create_round(
        task["id"],
        {
            "round_note": "客户补充整改资料",
            "_operator_user_id": reviewer["id"],
            "_trace_id": "persistence-test",
        },
    )
    assert second_round and second_round["round_no"] == 2

    files = store.add_task_files(
        task["id"],
        [
            {
                "file_name": "说明书.pdf",
                "file_type": "pdf",
                "content_type": "application/pdf",
                "file_size": 4,
                "sha256": "a" * 64,
                "storage_key": f"tasks/{task['id']}/manual.pdf",
                "_operator_user_id": reviewer["id"],
                "_trace_id": "persistence-test",
            }
        ],
    )
    assert files and files[0]["status"] == "uploaded"

    reloaded = SqlAlchemyStore(factory).get_task(task["id"])
    assert reloaded
    assert reloaded["product_model"] == "DSJ100/63/2x90"
    assert len(reloaded["rounds"]) == 2
    assert reloaded["files"][0]["file_name"] == "说明书.pdf"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OperationLog)) == 3


def test_database_store_starts_audit_and_queues_job(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "audit-run.db")
    reviewer = store.authenticate("liming", DemoStore.demo_password)
    assert reviewer

    task = store.create_task({"owner_user_id": reviewer["id"], "_operator_user_id": reviewer["id"]})
    run = store.start_audit(
        task["current_round_id"],
        {"_operator_user_id": reviewer["id"], "_trace_id": "audit-start-test"},
    )

    assert run and run["status"] == "queued"
    reloaded = store.get_task(task["id"])
    assert reloaded and reloaded["status"] == "auditing"
    assert reloaded["rounds"][0]["status"] == "auditing"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditRun)) == 1
        assert session.scalar(select(func.count()).select_from(QueueJob)) == 1


def test_database_store_persists_standard_catalog_and_round_snapshot(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "standards.db")
    reviewer = store.authenticate("liming", DemoStore.demo_password)
    assert reviewer
    standards = store.list_standards()
    assert standards and standards[0]["versions"]

    created = store.create_standard(
        {
            "standard_code": "AQ 9999",
            "standard_name": "数据库测试标准",
            "standard_type": "安全生产标准",
            "_operator_user_id": reviewer["id"],
        }
    )
    assert created
    version = store.create_standard_version(
        created["id"],
        {
            "version_label": "2026",
            "full_code": "AQ 9999-2026",
            "publish_date": datetime(2026, 1, 15, tzinfo=UTC).date(),
            "implement_date": datetime(2026, 7, 1, tzinfo=UTC).date(),
            "abolish_date": datetime(2030, 12, 31, tzinfo=UTC).date(),
            "_operator_user_id": reviewer["id"],
        },
    )
    assert version
    assert version["publish_date"] == "2026-01-15"
    assert version["implement_date"] == "2026-07-01"
    assert version["abolish_date"] == "2030-12-31"
    published = store.publish_standard_version(version["id"], {"_operator_user_id": reviewer["id"]})
    assert published and published["status"] == "active"

    task = store.create_task({"owner_user_id": reviewer["id"], "_operator_user_id": reviewer["id"]})
    selected = store.add_standard_to_round(
        task["current_round_id"],
        {"standard_version_id": version["id"], "_operator_user_id": reviewer["id"]},
    )
    assert selected
    confirmed = store.confirm_round_standard(
        task["current_round_id"],
        selected["id"],
        {"_operator_user_id": reviewer["id"]},
    )
    assert confirmed and confirmed["status"] == "confirmed"
    reloaded = store.get_task(task["id"])
    assert reloaded and reloaded["rounds"][0]["standards"][0]["standard_code"] == "AQ 9999-2026"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Standard)) == 6
        assert session.scalar(select(func.count()).select_from(StandardVersion)) == 6
        assert session.scalar(select(func.count()).select_from(RoundStandard)) == 1


def test_database_store_versions_parse_revisions_and_compares_clauses(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "standard-revisions.db")
    admin = store.authenticate("admin", DemoStore.demo_password)
    assert admin
    standard = store.list_standards()[0]
    original_version = standard["versions"][0]
    new_version = store.create_standard_version(
        standard["id"],
        {
            "version_label": "2099",
            "full_code": f"{standard['standard_code']}-2099",
            "_operator_user_id": admin["id"],
        },
    )
    assert new_version
    revision = store.create_standard_parse_revision(
        new_version["id"],
        {
            "impact_flag": "audit_impact",
            "clauses": [
                {
                    "clause_code": "5.3.2",
                    "title": "驱动功率配置",
                    "constraint_level": "必须",
                    "original_text": "驱动功率应满足新版设计输送能力。",
                },
                {
                    "clause_code": "7.1",
                    "title": "检验要求",
                    "constraint_level": "必须",
                    "original_text": "出厂前必须完成检验。",
                },
            ],
            "_operator_user_id": admin["id"],
        },
    )
    assert revision and revision["revision_no"] == "P2"
    published = store.publish_standard_parse_revision(revision["id"], {"_operator_user_id": admin["id"]})
    assert published and published["status"] == "published"

    compared = store.compare_standard_versions(original_version["id"], new_version["id"])
    assert compared and compared["summary"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 0}
    abolished = store.abolish_standard_version(
        original_version["id"],
        {
            "abolish_date": datetime(2099, 7, 1, tzinfo=UTC).date(),
            "superseded_by_version_id": new_version["id"],
            "_operator_user_id": admin["id"],
        },
    )
    assert abolished and abolished["status"] == "obsolete"
    assert abolished["superseded_by_id"] == new_version["id"]

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(StandardParseRevision)) == 7
        assert session.scalar(select(func.count()).select_from(StandardClause)) == 12


def test_database_store_persists_executor_and_rule_versions(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "rules.db")
    admin = store.authenticate("admin", DemoStore.demo_password)
    assert admin
    executors = store.list_executors()
    rules = store.list_rules()
    assert len(executors) == 9
    assert len(rules) == 4
    assert executors[0]["versions"]
    assert rules[0]["versions"]

    created = store.create_rule(
        {
            "rule_code": "PRODUCT_MODEL_FORMAT",
            "rule_name": "产品型号格式检查",
            "rule_type": "deterministic",
            "executor_code": next(item["executor_code"] for item in executors if item["executor_code"] == "regex_format"),
            "default_issue_category": "format",
            "default_severity": "一般",
            "affects_suggested_conclusion": True,
            "_operator_user_id": admin["id"],
        }
    )
    assert created and created["status"] == "draft"
    version = store.create_rule_version(
        created["id"],
        {
            "parameters": {"pattern": "^[A-Z]"},
            "stage_code": "basic_info",
            "_operator_user_id": admin["id"],
        },
    )
    assert version and version["version_no"] == "v1.0"
    published = store.publish_rule_version(version["id"], {"_operator_user_id": admin["id"]})
    assert published and published["status"] == "published"
    reloaded = store.get_rule(created["id"])
    assert reloaded and reloaded["status"] == "published"
    assert reloaded["versions"][0]["executor_code"] == "regex_format"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutorDefinition)) == 9
        assert session.scalar(select(func.count()).select_from(ExecutorVersion)) == 9
        assert session.scalar(select(func.count()).select_from(RuleDefinition)) == 5
        assert session.scalar(select(func.count()).select_from(RuleVersion)) == 5


def test_database_store_persists_rule_packs_and_inherited_round_snapshot(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "rule-snapshot.db")
    reviewer = store.authenticate("liming", DemoStore.demo_password)
    assert reviewer
    task = store.create_task({"owner_user_id": reviewer["id"], "_operator_user_id": reviewer["id"]})
    store.add_task_files(
        task["id"],
        [
            {
                "file_name": "manual.pdf",
                "file_type": "pdf",
                "content_type": "application/pdf",
                "file_size": 6,
                "sha256": "b" * 64,
                "storage_key": f"tasks/{task['id']}/manual.pdf",
            },
            {
                "file_name": "inspection.docx",
                "file_type": "docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "file_size": 10,
                "sha256": "c" * 64,
                "storage_key": f"tasks/{task['id']}/inspection.docx",
            },
        ],
    )

    snapshot = store.assemble_round_rules(task["current_round_id"], {})
    assert snapshot and len(snapshot["rules"]) == 3
    repeated = store.assemble_round_rules(task["current_round_id"], {})
    assert repeated and [item["id"] for item in repeated["rules"]] == [item["id"] for item in snapshot["rules"]]

    next_round = store.create_round(task["id"], {"inherit_previous_snapshot": True})
    assert next_round
    inherited = store.list_round_rules(next_round["id"])
    assert inherited and {item["rule_version_id"] for item in inherited} == {
        item["rule_version_id"] for item in snapshot["rules"]
    }

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(RulePack)) == 3
        assert session.scalar(select(func.count()).select_from(RulePackItem)) == 4
        assert session.scalar(select(func.count()).select_from(RoundRule)) == 6


def test_database_store_persists_and_revokes_auth_session(tmp_path: Path) -> None:
    store, factory = _store(tmp_path / "auth-session.db")
    reviewer = store.authenticate("liming", DemoStore.demo_password)
    assert reviewer
    session_id = store.create_auth_session(reviewer["id"], datetime.now(UTC) + timedelta(hours=1))

    reloaded = SqlAlchemyStore(factory)
    assert reloaded.is_auth_session_active(session_id, reviewer["id"])
    assert reloaded.revoke_auth_session(session_id, reviewer["id"])
    assert not store.is_auth_session_active(session_id, reviewer["id"])


def test_database_store_api_uploads_file_and_enforces_owner_scope(tmp_path: Path) -> None:
    store, _factory = _store(tmp_path / "api.db")
    storage = InMemoryObjectStorage()

    with TestClient(create_app(store=store, object_storage=storage)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": "liming", "password": DemoStore.demo_password},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                "customer_name": "接口测试企业",
                "product_name": "矿用开关",
                "product_model": "KBZ-500/1140",
            },
        )
        assert created.status_code == 201
        task_id = created.json()["data"]["id"]

        uploaded = client.post(
            f"/api/v1/tasks/{task_id}/files",
            headers=headers,
            files={"files": ("说明书.pdf", b"%PDF", "application/pdf")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["data"]["files"][0]["file_size"] == 4
        assert list(storage.objects.values()) == [b"%PDF"]

        duplicate = client.post(
            f"/api/v1/tasks/{task_id}/files",
            headers=headers,
            files={"files": ("说明书-副本.pdf", b"%PDF", "application/pdf")},
        )
        assert duplicate.status_code == 200
        assert len(storage.objects) == 1

        tasks = client.get("/api/v1/tasks", headers=headers).json()["data"]["items"]
        assert all(task["owner_user_id"] == login.json()["user_id"] for task in tasks)

        admin_login = client.post(
            "/api/v1/auth/login",
            json={"login_name": "admin", "password": DemoStore.demo_password},
        )
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        logs = client.get("/api/v1/logs", headers=admin_headers)
        assert logs.status_code == 200
        assert {item["action_code"] for item in logs.json()["data"]} >= {"task.create", "task_file.upload"}
