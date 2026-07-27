from fastapi.testclient import TestClient

from coal_platform.main import create_app
from coal_platform.storage import InMemoryObjectStorage
from coal_platform.store import DemoStore


def _client() -> TestClient:
    return TestClient(create_app(store=DemoStore.seed(), object_storage=InMemoryObjectStorage()))


def _login(client: TestClient, login_name: str = "liming") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_name": login_name, "password": DemoStore.demo_password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_logout_revokes_current_access_token() -> None:
    with _client() as client:
        headers = _login(client)
        other_headers = _login(client)
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        response = client.post("/api/v1/auth/logout", headers=headers)

        assert response.status_code == 200
        assert response.json()["data"] is True
        rejected = client.get("/api/v1/auth/me", headers=headers)
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "UNAUTHORIZED"
        assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 200


def test_health_and_openapi_are_available() -> None:
    with _client() as client:
        response = client.get("/api/v1/healthz", headers={"X-Trace-Id": "test-trace"})

        assert response.status_code == 200
        assert response.json() == {
            "code": "OK",
            "message": "success",
            "data": {"status": "ok"},
            "trace_id": "test-trace",
        }
        assert response.headers["X-Trace-Id"] == "test-trace"
        assert client.get("/api/v1/readyz").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200
        unauthorized = client.get("/api/v1/tasks")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["WWW-Authenticate"] == "Bearer"


def test_login_rejects_invalid_password_and_returns_current_user() -> None:
    with _client() as client:
        rejected = client.post(
            "/api/v1/auth/login",
            json={"login_name": "liming", "password": "wrong-password"},
        )
        assert rejected.status_code == 401

        headers = _login(client)
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["login_name"] == "liming"


def test_task_creation_creates_the_initial_round() -> None:
    with _client() as client:
        headers = _login(client)
        response = client.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                "customer_name": "测试企业",
                "product_name": "带式输送机",
                "product_model": "DSJ80/40/2x75",
            },
        )

        assert response.status_code == 201
        task = response.json()["data"]
        assert task["product_model"] == "DSJ80/40/2x75"
        assert len(task["rounds"]) == 1
        assert task["rounds"][0]["id"] == task["current_round_id"]


def test_start_audit_updates_task_and_round_status() -> None:
    with _client() as client:
        headers = _login(client)
        created = client.post("/api/v1/tasks", headers=headers, json={})
        assert created.status_code == 201
        task = created.json()["data"]
        round_id = task["current_round_id"]

        started = client.post(f"/api/v1/rounds/{round_id}/audit/start", headers=headers)

        assert started.status_code == 202
        run = started.json()["data"]
        assert run["status"] == "queued"
        detail = client.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()["data"]
        assert detail["status"] == "auditing"
        assert detail["rounds"][0]["status"] == "auditing"


def test_first_phase_read_endpoints_return_demo_data() -> None:
    with _client() as client:
        headers = _login(client)
        for path in ("/api/v1/tasks", "/api/v1/standards", "/api/v1/rules", "/api/v1/executors"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, path
            assert response.json()["data"], path


def test_reviewer_cannot_access_admin_user_list() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        assert client.get("/api/v1/users", headers=reviewer_headers).status_code == 403

        admin_headers = _login(client, login_name="admin")
        assert client.get("/api/v1/users", headers=admin_headers).status_code == 200


def test_missing_resource_uses_platform_error_shape() -> None:
    with _client() as client:
        response = client.get("/api/v1/tasks/not-found", headers=_login(client))

        assert response.status_code == 404
        assert response.json()["code"] == "RESOURCE_NOT_FOUND"
        assert response.json()["trace_id"]


def test_adding_standard_requires_an_existing_round() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/rounds/not-found/standards",
            headers=_login(client),
            json={"standard_code": "MT/T 820-2023"},
        )

        assert response.status_code == 404
