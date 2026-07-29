from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from coal_platform.main import create_app
from coal_platform.ocr import OCRLine
from coal_platform.storage import InMemoryObjectStorage
from coal_platform.store import DemoStore


def _client(ocr_backend=None) -> TestClient:
    return TestClient(
        create_app(
            store=DemoStore.seed(),
            object_storage=InMemoryObjectStorage(),
            ocr_backend=ocr_backend,
        )
    )


def _login(client: TestClient, login_name: str = "liming") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_name": login_name, "password": DemoStore.demo_password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _docx_content(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class _FakeOCRBackend:
    engine_name = "fake-ocr"

    def recognize(self, image_content: bytes) -> list[OCRLine]:
        assert image_content.startswith(b"\x89PNG")
        return [OCRLine("防爆标志 Ex db I Mb", 0.95, 10, 10, 100, 20)]


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


def test_password_change_revokes_all_sessions_and_replaces_password() -> None:
    with _client() as client:
        headers = _login(client)
        other_headers = _login(client)

        incorrect = client.patch(
            "/api/v1/auth/password",
            headers=headers,
            json={"current_password": "incorrect", "new_password": "new-coal-password"},
        )
        assert incorrect.status_code == 400
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        changed = client.patch(
            "/api/v1/auth/password",
            headers=headers,
            json={"current_password": DemoStore.demo_password, "new_password": "new-coal-password"},
        )
        assert changed.status_code == 200
        assert changed.json()["data"] is True
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"login_name": "liming", "password": DemoStore.demo_password},
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"login_name": "liming", "password": "new-coal-password"},
        ).status_code == 200


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


def test_metrics_require_dedicated_token_and_export_runtime_state() -> None:
    with _client() as client:
        assert client.get("/api/v1/metrics").status_code == 401
        response = client.get(
            "/api/v1/metrics",
            headers={"Authorization": "Bearer development-metrics-token"},
        )
        assert response.status_code == 200
        assert "coal_http_requests_total" in response.text
        assert 'coal_queue_jobs{status="queued"}' in response.text
        assert "coal_model_failure_ratio" in response.text


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


def test_uploaded_document_can_be_parsed_and_queried_as_evidence_blocks() -> None:
    with _client() as client:
        headers = _login(client)
        admin_headers = _login(client, login_name="admin")
        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        uploaded = client.post(
            f"/api/v1/tasks/{task['id']}/files",
            headers=headers,
            files={
                "files": (
                    "specification.docx",
                    _docx_content("Product model: KBZ-500/1140"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        ).json()["data"]
        file_item = uploaded["files"][0]
        parse_job = uploaded["parse_jobs"][0]
        assert parse_job["job_type"] == "document_parse"

        completed = client.post(f"/api/v1/jobs/{parse_job['id']}/run", headers=admin_headers)
        assert completed.status_code == 200
        assert completed.json()["data"]["result"]["block_count"] == 1
        detail = client.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()["data"]
        parsed_file = next(item for item in detail["files"] if item["id"] == file_item["id"])
        assert parsed_file["status"] == "parsed"
        assert parsed_file["parse_summary"]["parser"] == "python-docx"
        assert parsed_file["parse_summary"]["quality_metrics"]["quality_score"] == 1.0

        blocks = client.get(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/blocks",
            headers=headers,
        )
        assert blocks.status_code == 200
        block = blocks.json()["data"][0]
        assert block["content_text"] == "Product model: KBZ-500/1140"
        assert block["source_ref"] == "docx:paragraph:1"
        revised = client.patch(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/blocks/{block['id']}",
            headers=headers,
            json={"content_text": "Product model: KBZ-500/1140A", "reason": "人工核对原文"},
        )
        assert revised.status_code == 200
        assert revised.json()["data"]["content_text"].endswith("1140A")
        reviewed = client.post(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/parse-review",
            headers=headers,
            json={"decision": "accepted", "reason": "证据块已完成核对"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["data"]["parse_summary"]["quality_review"]["status"] == "accepted"


def test_scanned_pdf_ocr_blocks_include_page_bbox_and_confidence() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    output = BytesIO()
    writer.write(output)
    with _client(ocr_backend=_FakeOCRBackend()) as client:
        headers = _login(client)
        admin_headers = _login(client, login_name="admin")
        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        uploaded = client.post(
            f"/api/v1/tasks/{task['id']}/files",
            headers=headers,
            files={"files": ("scan.pdf", output.getvalue(), "application/pdf")},
        ).json()["data"]
        file_item = uploaded["files"][0]
        job = uploaded["parse_jobs"][0]

        completed = client.post(f"/api/v1/jobs/{job['id']}/run", headers=admin_headers)
        assert completed.status_code == 200
        assert completed.json()["data"]["result"]["needs_ocr"] is False
        blocks = client.get(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/blocks",
            headers=headers,
        ).json()["data"]
        assert blocks[0]["block_type"] == "ocr_line"
        assert blocks[0]["content_text"] == "防爆标志 Ex db I Mb"
        assert blocks[0]["confidence"] == 0.95
        assert blocks[0]["bbox"]["unit"] == "pt"
        pages = client.get(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/pages",
            headers=headers,
        )
        assert pages.status_code == 200
        assert pages.json()["data"][0]["thumbnail_storage_key"]
        thumbnail = client.get(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/pages/1/thumbnail",
            headers=headers,
        )
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/png"
        region = client.get(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/pages/1/region",
            headers=headers,
            params={"x": 0, "y": 0, "width": 100, "height": 50},
        )
        assert region.status_code == 200
        assert region.headers["content-type"] == "image/png"


def test_document_parse_failure_is_visible_and_retryable() -> None:
    with _client() as client:
        headers = _login(client)
        admin_headers = _login(client, login_name="admin")
        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        uploaded = client.post(
            f"/api/v1/tasks/{task['id']}/files",
            headers=headers,
            files={"files": ("invalid.pdf", b"not-a-pdf", "application/pdf")},
        ).json()["data"]
        file_item = uploaded["files"][0]
        parse_job = uploaded["parse_jobs"][0]

        failed = client.post(f"/api/v1/jobs/{parse_job['id']}/run", headers=admin_headers)
        assert failed.status_code == 422
        detail = client.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()["data"]
        failed_file = next(item for item in detail["files"] if item["id"] == file_item["id"])
        assert failed_file["status"] == "parse_failed"
        assert failed_file["parse_summary"]["error"]["code"] == "DOCUMENT_PARSE_FAILED"

        retried = client.post(
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/retry-parse",
            headers=headers,
        )
        assert retried.status_code == 200
        assert retried.json()["data"]["status"] == "parse_pending"
        assert retried.json()["data"]["parse_job"]["id"] != parse_job["id"]


def test_replaced_file_rejects_stale_running_parse_result() -> None:
    store = DemoStore.seed()
    task = store.create_task({})
    original = store.add_task_files(
        task["id"],
        [
            {
                "file_name": "original.txt",
                "file_type": "txt",
                "file_size": 8,
                "sha256": "a" * 64,
                "storage_key": "tasks/original.txt",
            }
        ],
    )[0]
    old_job = store.create_task_file_parse_job(task["id"], original["id"], {})
    assert old_job
    store.update_queue_job(old_job["id"], {"status": "running"})

    replaced = store.replace_task_file(
        task["id"],
        original["id"],
        {
            "file_name": "replacement.txt",
            "file_type": "txt",
            "content_type": "text/plain",
            "file_size": 11,
            "sha256": "b" * 64,
            "storage_key": "tasks/replacement.txt",
        },
    )
    assert replaced
    new_job = store.create_task_file_parse_job(task["id"], original["id"], {})
    assert new_job and new_job["id"] != old_job["id"]
    stale = store.complete_task_file_parse(
        original["id"],
        [{"page_no": 1, "block_type": "paragraph", "content_text": "stale"}],
        {"block_count": 1},
        {"_storage_key": "tasks/original.txt"},
    )
    assert stale is None
    current = store.get_task(task["id"])
    assert current["files"][0]["status"] == "parse_pending"
    assert store.list_task_file_blocks(task["id"], original["id"]) == []


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
        admin_headers = _login(client, login_name="admin")
        completed = client.post(f"/api/v1/jobs/{run['job_id']}/run", headers=admin_headers)
        assert completed.status_code == 200
        assert completed.json()["data"]["status"] == "succeeded"
        detail = client.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()["data"]
        assert detail["status"] == "waiting_review"
        assert detail["rounds"][0]["status"] == "waiting_review"


def test_admin_can_run_a_rule_execution_and_create_issue() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        created = client.post("/api/v1/tasks", headers=reviewer_headers, json={}).json()["data"]
        client.post(f"/api/v1/rounds/{created['current_round_id']}/rules/assemble", headers=reviewer_headers, json={})
        started = client.post(f"/api/v1/rounds/{created['current_round_id']}/audit/start", headers=reviewer_headers)
        assert started.status_code == 202
        executions = client.get(
            f"/api/v1/rounds/{created['current_round_id']}/rule-executions", headers=reviewer_headers
        ).json()["data"]
        admin_headers = _login(client, login_name="admin")
        execution = next(item for item in executions if item["status"] == "pending")
        result = client.post(f"/api/v1/rule-executions/{execution['id']}/run", headers=admin_headers)
        assert result.status_code == 200
        assert result.json()["data"]["status"] in {"succeeded", "failed", "unable_to_determine"}
        attempts = client.get(f"/api/v1/rule-executions/{execution['id']}/attempts", headers=admin_headers)
        assert attempts.status_code == 200
        assert len(attempts.json()["data"]) == 1


def test_first_phase_read_endpoints_return_demo_data() -> None:
    with _client() as client:
        headers = _login(client)
        for path in ("/api/v1/tasks", "/api/v1/standards", "/api/v1/rules", "/api/v1/executors"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, path
            assert response.json()["data"], path


def test_admin_can_manage_model_configuration_without_exposing_api_key() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        assert client.post(
            "/api/v1/settings/models", headers=reviewer_headers,
            json={"provider_code": "test", "provider_name": "测试", "base_url": "https://model.test", "model_code": "demo", "model_kind": "text", "api_key": "secret"},
        ).status_code == 403
        admin_headers = _login(client, login_name="admin")
        created = client.post(
            "/api/v1/settings/models", headers=admin_headers,
            json={"provider_code": "test", "provider_name": "测试", "base_url": "https://model.test", "model_code": "demo", "model_kind": "text", "api_key": "secret"},
        )
        assert created.status_code == 201
        model = created.json()["data"]
        assert model["api_key_configured"] is True
        assert "api_key" not in model
        duplicate = client.post(
            "/api/v1/settings/models", headers=admin_headers,
            json={"provider_code": "test", "provider_name": "测试", "base_url": "https://model.test", "model_code": "demo", "model_kind": "text", "api_key": "secret"},
        )
        assert duplicate.status_code == 409
        updated = client.patch(f"/api/v1/settings/models/{model['id']}", headers=admin_headers, json={"status": "disabled"})
        assert updated.status_code == 200
        assert updated.json()["data"]["status"] == "disabled"


def test_admin_can_test_model_connection_and_read_call_audit() -> None:
    store = DemoStore.seed()
    model = store.create_model_config({"provider_code": "qianfan", "provider_name": "百度千帆", "base_url": "https://qianfan.test/v2", "model_code": "deepseek-v4-pro", "model_kind": "text", "api_key": "secret"})
    assert model

    class FakeGateway:
        def test_connection(self, config_id: str, *, trace_id: str | None = None) -> dict:
            store.record_model_call({"model_config_id": config_id, "request_id": "request-1", "trace_id": trace_id, "operation": "chat", "status": "succeeded", "attempt_count": 1, "latency_ms": 10, "http_status": 200, "token_usage": {}})
            return {"reachable": True, "model_code": "deepseek-v4-pro", "request_id": "request-1"}

        def close(self) -> None:
            pass

    with TestClient(create_app(store=store, object_storage=InMemoryObjectStorage(), model_gateway=FakeGateway())) as client:
        reviewer_headers = _login(client)
        assert client.post(f"/api/v1/settings/models/{model['id']}/test", headers=reviewer_headers).status_code == 403
        admin_headers = _login(client, login_name="admin")
        tested = client.post(f"/api/v1/settings/models/{model['id']}/test", headers=admin_headers)
        assert tested.status_code == 200
        assert tested.json()["data"]["reachable"] is True
        logs = client.get("/api/v1/settings/model-call-logs", headers=admin_headers)
        assert logs.status_code == 200
        assert logs.json()["data"][0]["request_id"] == "request-1"


def test_admin_can_manage_categories_templates_and_system_parameters() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        assert client.post("/api/v1/settings/issue-categories", headers=reviewer_headers, json={"code": "new", "name": "新分类"}).status_code == 403
        admin_headers = _login(client, login_name="admin")
        category = client.post("/api/v1/settings/issue-categories", headers=admin_headers, json={"code": "new", "name": "新分类", "default_severity": "提示"})
        assert category.status_code == 201
        assert category.json()["data"]["code"] == "new"
        template = client.post("/api/v1/settings/report-templates", headers=admin_headers, json={"template_code": "new_template", "template_name": "新模板", "template_body": "{{ report_no }}"})
        assert template.status_code == 201
        assert template.json()["data"]["template_code"] == "new_template"
        parameter = client.put("/api/v1/settings/system-parameters/retry_limit", headers=admin_headers, json={"param_value": {"value": 5}})
        assert parameter.status_code == 200
        assert parameter.json()["data"]["param_value"]["value"] == 5


def test_reviewer_can_create_manual_issue_with_evidence() -> None:
    with _client() as client:
        headers = _login(client)
        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        created = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/issues",
            headers=headers,
            json={"title": "人工发现问题", "description": "资料页码与目录不一致", "category_code": "document_completeness", "evidence": [{"evidence_type": "customer", "page_no": 3, "excerpt_text": "目录缺少章节"}]},
        )
        assert created.status_code == 201
        issue = created.json()["data"]
        assert issue["issue_code"] == "MANUAL-0001"
        assert issue["sources"][0]["source_type"] == "manual"
        assert issue["evidence"][0]["page_no"] == 3


def test_admin_monitoring_reports_real_queue_counts_and_alerts() -> None:
    with _client() as client:
        headers = _login(client, login_name="admin")
        monitoring = client.get("/api/v1/monitoring", headers=headers)
        assert monitoring.status_code == 200
        assert {"queue_waiting", "queue_running", "queue_failed", "alerts_new"} <= monitoring.json()["data"].keys()
        alerts = client.get("/api/v1/monitoring/alerts", headers=headers)
        assert alerts.status_code == 200
        assert isinstance(alerts.json()["data"], list)


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


def test_standard_catalog_and_round_snapshot_workflow() -> None:
    with _client() as client:
        headers = _login(client)
        standards = client.get("/api/v1/standards", headers=headers)
        assert standards.status_code == 200
        standard = standards.json()["data"][0]
        version = standard["versions"][0]

        detail = client.get(f"/api/v1/standards/{standard['id']}", headers=headers)
        clauses = client.get(f"/api/v1/standard-versions/{version['id']}/clauses", headers=headers)
        assert detail.status_code == 200
        assert clauses.status_code == 200
        assert clauses.json()["data"]

        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        selected = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/standards",
            headers=headers,
            json={"standard_version_id": version["id"], "source_type": "manual_selection"},
        )
        assert selected.status_code == 201
        selected_id = selected.json()["data"]["id"]
        confirmed = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/standards/{selected_id}/confirm",
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["data"]["status"] == "confirmed"
        dynamic_items = client.get(
            f"/api/v1/rounds/{task['current_round_id']}/dynamic-items", headers=headers
        )
        assert dynamic_items.status_code == 200
        assert dynamic_items.json()["data"]
        dynamic_id = dynamic_items.json()["data"][0]["id"]
        dynamic_confirmed = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/dynamic-items/{dynamic_id}/confirm",
            headers=headers,
            json={"reason": "条款适用于当前产品"},
        )
        assert dynamic_confirmed.status_code == 200
        assert dynamic_confirmed.json()["data"]["applicability_status"] == "applicable"
        coverage = client.get(f"/api/v1/rounds/{task['current_round_id']}/coverage", headers=headers)
        assert coverage.json()["data"]["summary"]["applicable"] == 1

        round_standards = client.get(
            f"/api/v1/rounds/{task['current_round_id']}/standards",
            headers=headers,
        )
        assert round_standards.json()["data"][0]["standard_version_id"] == version["id"]


def test_only_admin_can_create_and_publish_standard_versions() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        payload = {
            "standard_code": "AQ 9999",
            "standard_name": "接口测试标准",
            "standard_type": "安全生产标准",
        }
        assert client.post("/api/v1/standards", headers=reviewer_headers, json=payload).status_code == 403

        admin_headers = _login(client, login_name="admin")
        seeded_standard = client.get("/api/v1/standards", headers=admin_headers).json()["data"][0]
        seeded_version = client.post(
            f"/api/v1/standards/{seeded_standard['id']}/versions",
            headers=admin_headers,
            json={"version_label": "2099", "full_code": f"{seeded_standard['standard_code']}-2099"},
        )
        assert seeded_version.status_code == 201

        created = client.post("/api/v1/standards", headers=admin_headers, json=payload)
        assert created.status_code == 201
        standard_id = created.json()["data"]["id"]
        version = client.post(
            f"/api/v1/standards/{standard_id}/versions",
            headers=admin_headers,
            json={
                "version_label": "2026",
                "full_code": "AQ 9999-2026",
                "publish_date": "2026-01-15",
                "implement_date": "2026-07-01",
                "abolish_date": "2030-12-31",
            },
        )
        assert version.status_code == 201
        assert version.json()["data"]["publish_date"] == "2026-01-15"
        assert version.json()["data"]["implement_date"] == "2026-07-01"
        assert version.json()["data"]["abolish_date"] == "2030-12-31"
        published = client.post(
            f"/api/v1/standard-versions/{version.json()['data']['id']}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200
        assert published.json()["data"]["status"] == "active"

        duplicate = client.post(
            f"/api/v1/standards/{standard_id}/versions",
            headers=admin_headers,
            json={"version_label": "2026", "full_code": "AQ 9999-2026"},
        )
        assert duplicate.status_code == 409

        missing_standard = client.post(
            "/api/v1/standards/not-found/versions",
            headers=admin_headers,
            json={"version_label": "2026"},
        )
        assert missing_standard.status_code == 404


def test_admin_can_queue_rule_test_run() -> None:
    with _client() as client:
        admin_headers = _login(client, login_name="admin")
        rules = client.get("/api/v1/rules", headers=admin_headers).json()["data"]
        version = next(version for rule in rules for version in rule.get("versions", []) if version["status"] == "published")
        response = client.post(
            f"/api/v1/rule-versions/{version['id']}/test-runs",
            headers=admin_headers,
            json={"input_payload": {"product_model": "KBZ-500/1140"}},
        )
        assert response.status_code == 202
        job = response.json()["data"]
        assert job["job_type"] == "rule_test_run"
        assert job["status"] == "queued"
        assert job["payload"]["dry_run"] is True
        completed = client.post(f"/api/v1/jobs/{job['id']}/run", headers=admin_headers)
        assert completed.status_code == 200
        assert completed.json()["data"]["status"] == "succeeded"
        assert completed.json()["data"]["result"]["outcome"] in {
            "passed", "failed", "unable_to_determine"
        }


def test_admin_can_cancel_waiting_job_but_cannot_cancel_it_twice() -> None:
    with _client() as client:
        admin_headers = _login(client, login_name="admin")
        reviewer_headers = _login(client)
        rules = client.get("/api/v1/rules", headers=admin_headers).json()["data"]
        version = next(version for rule in rules for version in rule.get("versions", []) if version["status"] == "published")
        job = client.post(
            f"/api/v1/rule-versions/{version['id']}/test-runs",
            headers=admin_headers,
            json={},
        ).json()["data"]

        assert client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=reviewer_headers).status_code == 403
        canceled = client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=admin_headers)
        assert canceled.status_code == 200
        assert canceled.json()["data"]["status"] == "canceled"
        assert canceled.json()["data"]["finished_at"]
        assert client.post(f"/api/v1/jobs/{job['id']}/run", headers=admin_headers).status_code == 422
        assert client.post(f"/api/v1/jobs/{job['id']}/retry", headers=admin_headers).status_code == 409
        assert client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=admin_headers).status_code == 409
        assert client.post("/api/v1/jobs/not-found/cancel", headers=admin_headers).status_code == 404


def test_standard_parse_revision_comparison_and_abolish_workflow() -> None:
    with _client() as client:
        admin_headers = _login(client, login_name="admin")
        standard = client.get("/api/v1/standards", headers=admin_headers).json()["data"][0]
        original_version = standard["versions"][0]
        new_version = client.post(
            f"/api/v1/standards/{standard['id']}/versions",
            headers=admin_headers,
            json={"version_label": "2099", "full_code": f"{standard['standard_code']}-2099"},
        ).json()["data"]

        revision = client.post(
            f"/api/v1/standard-versions/{new_version['id']}/parse-revisions",
            headers=admin_headers,
            json={
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
            },
        )
        assert revision.status_code == 201
        assert revision.json()["data"]["revision_no"] == "P2"

        published = client.post(
            f"/api/v1/standard-parse-revisions/{revision.json()['data']['id']}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200
        revisions = client.get(
            f"/api/v1/standard-versions/{new_version['id']}/parse-revisions",
            headers=admin_headers,
        ).json()["data"]
        assert [item["status"] for item in revisions] == ["draft", "published"]

        compared = client.get(
            f"/api/v1/standard-versions/{original_version['id']}/compare/{new_version['id']}",
            headers=admin_headers,
        )
        assert compared.status_code == 200
        assert compared.json()["data"]["summary"] == {
            "added": 1,
            "removed": 1,
            "modified": 1,
            "unchanged": 0,
        }

        abolished = client.post(
            f"/api/v1/standard-versions/{original_version['id']}/abolish",
            headers=admin_headers,
            json={"abolish_date": "2099-07-01", "superseded_by_version_id": new_version["id"]},
        )
        assert abolished.status_code == 200
        assert abolished.json()["data"]["status"] == "obsolete"
        assert abolished.json()["data"]["superseded_by_id"] == new_version["id"]


def test_rule_and_executor_catalog_workflow() -> None:
    with _client() as client:
        reviewer_headers = _login(client)
        executors = client.get("/api/v1/executors", headers=reviewer_headers)
        rules = client.get("/api/v1/rules", headers=reviewer_headers)
        assert executors.status_code == 200
        assert rules.status_code == 200
        assert executors.json()["data"][0]["versions"]
        assert rules.json()["data"][0]["versions"]
        executor_code = next(item["executor_code"] for item in executors.json()["data"] if item["executor_code"] == "regex_format")
        assert client.get(f"/api/v1/executors/{executor_code}/versions", headers=reviewer_headers).status_code == 200
        assert client.post(
            "/api/v1/rules",
            headers=reviewer_headers,
            json={
                "rule_code": "REVIEWER_CANNOT_CREATE",
                "rule_name": "权限测试规则",
                "rule_type": "deterministic",
                "executor_code": executor_code,
                "default_issue_category": "technical_compliance",
                "default_severity": "一般",
            },
        ).status_code == 403

        admin_headers = _login(client, login_name="admin")
        created_executor = client.post(
            "/api/v1/executors",
            headers=admin_headers,
            json={
                "executor_code": "custom_consistency",
                "executor_name": "自定义一致性检查",
                "executor_kind": "builtin",
                "input_type": "rule_input",
                "output_type": "rule_result",
                "runtime_mode": "worker",
            },
        )
        assert created_executor.status_code == 201
        assert created_executor.json()["data"]["versions"] == []
        created_executor_version = client.post(
            "/api/v1/executors/custom_consistency/versions",
            headers=admin_headers,
            json={
                "version_no": "1.0.0",
                "parameter_schema": {"type": "object"},
                "result_schema": {"type": "object"},
                "entrypoint": "coal_platform.executors.custom_consistency:execute",
                "image_version": "worker-2026.07",
            },
        )
        assert created_executor_version.status_code == 201
        assert created_executor_version.json()["data"]["executor_code"] == "custom_consistency"
        assert client.post(
            "/api/v1/executors/custom_consistency/versions",
            headers=reviewer_headers,
            json={"version_no": "2.0.0"},
        ).status_code == 403

        created = client.post(
            "/api/v1/rules",
            headers=admin_headers,
            json={
                "rule_code": "PRODUCT_MODEL_FORMAT",
                "rule_name": "产品型号格式检查",
                "rule_type": "deterministic",
                "executor_code": executor_code,
                "default_issue_category": "format",
                "default_severity": "一般",
                "affects_suggested_conclusion": True,
            },
        )
        assert created.status_code == 201
        rule = created.json()["data"]
        version = client.post(
            f"/api/v1/rules/{rule['id']}/versions",
            headers=admin_headers,
            json={"parameters": {"pattern": "^[A-Z]"}, "stage_code": "basic_info"},
        )
        assert version.status_code == 201
        published = client.post(
            f"/api/v1/rule-versions/{version.json()['data']['id']}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200
        assert published.json()["data"]["status"] == "published"
        detail = client.get(f"/api/v1/rules/{rule['id']}", headers=reviewer_headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["status"] == "published"


def test_rule_publish_rejects_invalid_executor_parameters() -> None:
    with _client() as client:
        admin_headers = _login(client, login_name="admin")
        rule = client.post(
            "/api/v1/rules",
            headers=admin_headers,
            json={
                "rule_code": "INVALID_REGEX_PARAMETERS",
                "rule_name": "无效正则参数测试",
                "rule_type": "deterministic",
                "executor_code": "regex_format",
                "default_issue_category": "format",
                "default_severity": "一般",
            },
        ).json()["data"]
        version = client.post(
            f"/api/v1/rules/{rule['id']}/versions",
            headers=admin_headers,
            json={"parameters": {"pattern": 42}, "stage_code": "single_file_review"},
        ).json()["data"]

        validation = client.post(
            f"/api/v1/rule-versions/{version['id']}/validate",
            headers=admin_headers,
        )
        published = client.post(
            f"/api/v1/rule-versions/{version['id']}/publish",
            headers=admin_headers,
        )

        assert validation.status_code == 200
        assert validation.json()["data"]["valid"] is False
        assert validation.json()["data"]["errors"][0]["path"] == "parameters.pattern"
        assert published.status_code == 422
        assert published.json()["code"] == "VALIDATION_ERROR"


def test_rule_packs_and_round_rule_snapshot_workflow() -> None:
    with _client() as client:
        headers = _login(client)
        admin_headers = _login(client, login_name="admin")
        stages = client.get("/api/v1/settings/audit-stages", headers=headers)
        packs = client.get("/api/v1/rule-packs", headers=headers)
        assert stages.status_code == 200
        assert [item["order_no"] for item in stages.json()["data"]] == list(range(10, 100, 10))
        assert packs.status_code == 200
        assert len(packs.json()["data"]) == 3

        invalid_pack = client.post(
            "/api/v1/rule-packs",
            headers=admin_headers,
            json={"pack_code": "INVALID_STAGE", "pack_name": "无效阶段", "stage_code": "free_form"},
        )
        assert invalid_pack.status_code == 422
        assert invalid_pack.json()["detail"][0]["code"] == "INVALID_STAGE"

        task = client.post("/api/v1/tasks", headers=headers, json={}).json()["data"]
        uploaded = client.post(
            f"/api/v1/tasks/{task['id']}/files",
            headers=headers,
            files=[
                ("files", ("manual.pdf", b"manual", "application/pdf")),
                (
                    "files",
                    (
                        "inspection.docx",
                        b"inspection",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
            ],
        )
        assert uploaded.status_code == 200
        file_id = uploaded.json()["data"]["files"][0]["id"]
        classified = client.patch(
            f"/api/v1/tasks/{task['id']}/files/{file_id}",
            headers=headers,
            json={"file_type": "product_manual", "is_required": True},
        )
        assert classified.status_code == 200
        assert classified.json()["data"]["file_type"] == "product_manual"
        retried = client.post(f"/api/v1/tasks/{task['id']}/files/{file_id}/retry-parse", headers=headers)
        assert retried.status_code == 200
        assert retried.json()["data"]["status"] == "parse_pending"
        replaced = client.put(
            f"/api/v1/tasks/{task['id']}/files/{file_id}",
            headers=headers,
            files={"file": ("新版说明书.pdf", b"new-pdf", "application/pdf")},
        )
        assert replaced.status_code == 200
        assert replaced.json()["data"]["version_no"] == 2
        deleted = client.delete(f"/api/v1/tasks/{task['id']}/files/{file_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["data"]["status"] == "deleted"
        assert client.post(f"/api/v1/tasks/{task['id']}/files/{file_id}/retry-parse", headers=headers).status_code == 404

        assembled = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/rules/assemble",
            headers=headers,
            json={},
        )
        assert assembled.status_code == 200
        snapshot = assembled.json()["data"]
        assert snapshot["locked"] is True
        assert len(snapshot["rules"]) == 3
        assert {item["source_type"] for item in snapshot["rules"]} == {"global", "file_trigger"}

        started = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/audit/start",
            headers=headers,
        )
        assert started.status_code == 202
        executions = client.get(
            f"/api/v1/rounds/{task['current_round_id']}/rule-executions",
            headers=headers,
        )
        assert executions.status_code == 200
        assert len(executions.json()["data"]) == 3
        execution_id = executions.json()["data"][0]["id"]
        execution = client.get(f"/api/v1/rule-executions/{execution_id}", headers=headers)
        attempts = client.get(f"/api/v1/rule-executions/{execution_id}/attempts", headers=headers)
        assert execution.status_code == 200
        assert execution.json()["data"]["status"] == "pending"
        assert attempts.status_code == 200
        assert attempts.json()["data"] == []
        recorded = client.post(
            f"/api/v1/rule-executions/{execution_id}/attempts",
            headers=headers,
            json={
                "status": "failed",
                "error_payload": {"code": "TIMEOUT"},
                "output_payload": {"issue": {
                    "issue_code": "MODEL-INCONSISTENT",
                    "title": "产品型号不一致",
                    "description": "说明书与图纸型号不一致",
                    "severity": "严重",
                    "customer_evidence": {"page_no": 3, "excerpt_text": "型号为 A"},
                    "standard_evidence": {"excerpt_text": "型号应保持一致"},
                }},
                "elapsed_ms": 1200,
            },
        )
        assert recorded.status_code == 200
        assert recorded.json()["data"]["status"] == "failed"
        retried = client.post(f"/api/v1/rule-executions/{execution_id}/retry", headers=headers)
        assert retried.status_code == 200
        assert retried.json()["data"]["retry_count"] == 1
        second_execution_id = executions.json()["data"][1]["id"]
        conflicting = client.post(
            f"/api/v1/rule-executions/{second_execution_id}/attempts",
            headers=headers,
            json={"status": "failed", "output_payload": {"issue": {
                "issue_code": "MODEL-INCONSISTENT",
                "title": "产品型号不一致",
                "description": "另一规则发现同一问题",
                "severity": "一般",
                "customer_evidence": {"page_no": 8, "excerpt_text": "型号为 B"},
                "standard_evidence": {"excerpt_text": "型号应保持一致"},
            }}},
        )
        assert conflicting.status_code == 200
        issues = client.get(
            "/api/v1/issues", headers=headers, params={"round_id": task["current_round_id"]}
        )
        assert issues.status_code == 200
        assert len(issues.json()["data"]) == 1
        assert issues.json()["data"][0]["system_conclusion"] == "conflict_requires_review"
        assert len(issues.json()["data"][0]["sources"]) == 2
        assert len(issues.json()["data"][0]["evidence"]) == 4
        issue_id = issues.json()["data"][0]["id"]
        confirmed = client.post(
            f"/api/v1/issues/{issue_id}/confirm",
            headers=headers,
            json={"reason": "证据已人工核对"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["data"]["status"] == "confirmed"
        updated_issue = client.patch(
            f"/api/v1/issues/{issue_id}",
            headers=headers,
            json={"manual_conclusion": "已确认存在型号不一致", "reason": "完成证据复核"},
        )
        assert updated_issue.status_code == 200
        assert updated_issue.json()["data"]["manual_conclusion"] == "已确认存在型号不一致"
        closed = client.post(
            f"/api/v1/issues/{issue_id}/close", headers=headers, json={"reason": "已纳入整改清单"}
        )
        assert closed.status_code == 200
        assert closed.json()["data"]["status"] == "closed"
        local = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/audit/local-rerun",
            headers=headers,
            json={
                "affected_rule_codes": [snapshot["rules"][0]["rule_code"]],
                "reason": "补充客户文件后重跑",
                "input_change": {"file_ids": [uploaded.json()["data"]["files"][0]["id"]]},
            },
        )
        assert local.status_code == 202
        assert local.json()["data"]["run_scope"] == "local"
        assert local.json()["data"]["affected_rule_codes"] == [snapshot["rules"][0]["rule_code"]]
        progress = client.get(
            f"/api/v1/rounds/{task['current_round_id']}/audit/progress", headers=headers
        )
        assert progress.status_code == 200
        assert progress.json()["data"]["total"] == 1
        assert progress.json()["data"]["completed"] == 0
        publish_check = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/coverage/check", headers=headers
        )
        assert publish_check.status_code == 200
        assert publish_check.json()["data"]["can_publish"] is False
        assert "EXECUTION_INCOMPLETE" in {item["code"] for item in publish_check.json()["data"]["blockers"]}
        report = client.post(
            "/api/v1/reports",
            headers=headers,
            json={"round_id": task["current_round_id"], "report_type": "formal", "conclusion": "through"},
        )
        assert report.status_code == 201
        assert report.json()["data"]["status"] == "draft"
        report_detail = client.get(
            f"/api/v1/reports/{report.json()['data']['id']}", headers=headers
        )
        assert report_detail.status_code == 200
        assert report_detail.json()["data"]["report_no"] == report.json()["data"]["report_no"]
        assert report_detail.json()["data"]["content_snapshot"]["task"]["task_no"] == task["task_no"]
        preview = client.get(f"/api/v1/reports/{report.json()['data']['id']}/preview", headers=headers)
        assert preview.status_code == 200
        assert preview.json()["data"]["content"]["title"] == "煤矿安标技术文档审核报告"
        draft_artifacts = client.get(
            f"/api/v1/reports/{report.json()['data']['id']}/artifacts", headers=headers
        )
        assert draft_artifacts.status_code == 200
        assert draft_artifacts.json()["data"] == []
        blocked_publish = client.post(
            f"/api/v1/reports/{report.json()['data']['id']}/publish",
            headers=headers,
            json={"reason": "尝试发布"},
        )
        assert blocked_publish.status_code == 422
        assert blocked_publish.json()["detail"]["blockers"]

        repeated = client.post(
            f"/api/v1/rounds/{task['current_round_id']}/rules/assemble",
            headers=headers,
            json={},
        ).json()["data"]
        assert [item["id"] for item in repeated["rules"]] == [item["id"] for item in snapshot["rules"]]

        next_round = client.post(
            f"/api/v1/tasks/{task['id']}/rounds",
            headers=headers,
            json={"inherit_previous_snapshot": True},
        ).json()["data"]
        inherited = client.get(f"/api/v1/rounds/{next_round['id']}/rules", headers=headers).json()["data"]
        assert {item["rule_version_id"] for item in inherited} == {
            item["rule_version_id"] for item in snapshot["rules"]
        }
        assert {item["snapshot_no"] for item in inherited} != {snapshot["snapshot_no"]}
