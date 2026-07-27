from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from coal_platform.database import Base
from coal_platform.main import create_app
from coal_platform.models import AuditRun, OperationLog, QueueJob
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
