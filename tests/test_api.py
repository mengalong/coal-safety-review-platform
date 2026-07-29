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
