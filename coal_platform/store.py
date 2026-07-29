from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from hashlib import sha256
from threading import RLock
from uuid import uuid4

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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_date(value: date | str | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else value


def compare_clause_sets(left_clauses: list[dict], right_clauses: list[dict]) -> dict:
    compared_fields = (
        "title",
        "clause_level",
        "clause_type",
        "constraint_level",
        "original_text",
        "parameter_schema",
        "page_no",
        "bbox",
    )
    left_by_code = {item["clause_code"]: item for item in left_clauses}
    right_by_code = {item["clause_code"]: item for item in right_clauses}
    summary = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    changes = []
    for clause_code in sorted(set(left_by_code) | set(right_by_code)):
        before = left_by_code.get(clause_code)
        after = right_by_code.get(clause_code)
        if before is None:
            change_type = "added"
        elif after is None:
            change_type = "removed"
        elif any(before.get(field) != after.get(field) for field in compared_fields):
            change_type = "modified"
        else:
            summary["unchanged"] += 1
            continue
        summary[change_type] += 1
        changes.append({"clause_code": clause_code, "change_type": change_type, "before": before, "after": after})
    return {"summary": summary, "changes": changes}


def _clause_payload(payload: dict) -> dict:
    return {
        "id": str(uuid4()),
        "clause_code": payload["clause_code"],
        "title": payload.get("title"),
        "clause_level": payload.get("clause_level", 1),
        "clause_type": payload.get("clause_type", "requirement"),
        "constraint_level": payload.get("constraint_level", "待确认"),
        "original_text": payload.get("original_text", ""),
        "parameter_schema": payload.get("parameter_schema") or {},
        "page_no": payload.get("page_no"),
        "bbox": payload.get("bbox"),
        "confidence": payload.get("confidence", 0.0),
        "proof_status": payload.get("proof_status", "pending"),
    }


def next_version_no(version_numbers: list[str]) -> str:
    if not version_numbers:
        return "v1.0"
    parsed = []
    for version_no in version_numbers:
        parts = version_no.removeprefix("v").split(".")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            parsed.append((int(parts[0]), int(parts[1])))
    if parsed:
        major, minor = max(parsed)
        return f"v{major}.{minor + 1}"
    return f"v{len(version_numbers) + 1}.0"


class DemoStore:
    demo_password = "coal123456"

    def __init__(self) -> None:
        self._lock = RLock()
        self.users: dict[str, dict] = {}
        self.auth_sessions: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.audit_runs: dict[str, dict] = {}
        self.rule_executions: dict[str, dict] = {}
        self.impact_analyses: dict[str, dict] = {}
        self.standards: dict[str, dict] = {}
        self.standard_parse_revisions: dict[str, list[dict]] = {}
        self.rules: dict[str, dict] = {}
        self.rule_packs: dict[str, dict] = {}
        self.executors: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}
        self.issues: dict[str, dict] = {}
        self.model_configs: dict[str, dict] = {}
        self.system_parameters: dict[str, dict] = {}
        self._seed()

    @classmethod
    def seed(cls) -> DemoStore:
        return cls()

    def _seed(self) -> None:
        reviewer_id = self._add_user("liming", "李明", "reviewer")
        admin_id = self._add_user("admin", "陈静", "admin")

        self.executors = {
            "required_field": self._executor(
                "required_field", "字段非空检查", "builtin", "1.0.0", "published", "非空、分类、严重等级"
            ),
            "regex_format": self._executor("regex_format", "格式正则检查", "builtin", "1.0.0", "published", "正则表达式"),
            "date_validity": self._executor("date_validity", "日期有效性检查", "builtin", "1.0.0", "published", "基准日期、剩余月数"),
            "exact_compare": self._executor("exact_compare", "精确一致性", "builtin", "1.0.0", "published", "字段对字段"),
            "normalized_compare": self._executor(
                "normalized_compare", "归一化比较", "builtin", "1.0.0", "published", "归一化规则"
            ),
            "numeric_compare": self._executor("numeric_compare", "数值比较", "builtin", "1.0.0", "published", "阈值、单位"),
            "standard_status": self._executor("standard_status", "标准有效性", "builtin", "1.0.0", "published", "状态集合"),
            "semantic_compare": self._executor("semantic_compare", "语义符合性", "ai", "1.0.0", "published", "提示模板"),
            "evidence_required": self._executor(
                "evidence_required", "证据充分性", "builtin", "1.0.0", "published", "证据数量"
            ),
        }

        self.standards = {
            "standard_1": self._standard("GB/T 10595-2017", "带式输送机", "国家标准", "有效"),
            "standard_2": self._standard("MT/T 820-2023", "煤矿用带式输送机 技术条件", "行业标准", "有效"),
            "standard_3": self._standard("MT 820-2006", "煤矿用带式输送机 技术条件", "行业标准", "已废止"),
            "standard_4": self._standard("GB/T 191-2008", "包装储运图示标志", "国家标准", "有效"),
            "standard_5": self._standard("MT/T 154.1-2011", "煤矿机电产品型号编制方法", "行业标准", "有效"),
        }

        self.rules = {
            "rule_1": self._rule("CONTROLLED_PART_CERT_VALIDITY", "受控件安标证剩余有效期", "date_validity", "一般"),
            "rule_2": self._rule("PRODUCT_MODEL_CONSISTENCY", "产品型号跨文件一致性", "normalized_compare", "严重"),
            "rule_3": self._rule("STANDARD_VERSION_STATUS", "引用标准有效性检查", "standard_status", "一般"),
            "rule_4": self._rule("AI_EVIDENCE_REQUIRED", "AI 判断必须具备标准依据", "evidence_required", "提示"),
        }
        self.rule_packs = {}
        for pack_config in DEFAULT_RULE_PACKS:
            pack_id = str(uuid4())
            members = []
            for order_no, rule_code in enumerate(pack_config["rule_codes"], start=1):
                rule = next(item for item in self.rules.values() if item["rule_code"] == rule_code)
                version = rule["versions"][0]
                members.append(
                    {
                        "id": str(uuid4()),
                        "rule_pack_id": pack_id,
                        "rule_version_id": version["id"],
                        "rule_code": rule_code,
                        "version_no": version["version_no"],
                        "order_no": order_no,
                        "enabled": True,
                    }
                )
            self.rule_packs[pack_id] = {
                "id": pack_id,
                "pack_code": pack_config["pack_code"],
                "pack_name": pack_config["pack_name"],
                "stage_code": pack_config["stage_code"],
                "trigger_condition": deepcopy(pack_config["trigger_condition"]),
                "status": "published",
                "items": members,
            }

        self.tasks = {
            "task_1": self._task(
                task_no="SH-2026-000128",
                customer_name="晋北装备制造有限公司",
                product_name="带式输送机",
                product_model="DSJ80/40/2×75",
                owner_user_id=reviewer_id,
                owner_user_name="李明",
                status="waiting_review",
                current_round_no=2,
                current_round_id="round_1",
                final_conclusion=None,
                round_note="客户补充整改文件",
            ),
            "task_2": self._task(
                task_no="SH-2026-000127",
                customer_name="华煤机械科技有限公司",
                product_name="矿用隔爆型真空馈电开关",
                product_model="KBZ-400/1140",
                owner_user_id=reviewer_id,
                owner_user_name="李明",
                status="auditing",
                current_round_no=1,
                current_round_id="round_2",
                final_conclusion=None,
                round_note="文件解析中",
            ),
            "task_3": self._task(
                task_no="SH-2026-000124",
                customer_name="山西北辰机电有限公司",
                product_name="矿用本安型显示器",
                product_model="XH12",
                owner_user_id=admin_id,
                owner_user_name="陈静",
                status="waiting_standards",
                current_round_no=1,
                current_round_id="round_3",
                final_conclusion=None,
                round_note="待确认标准",
            ),
        }

        self.issues = {
            "issue_1": self._issue(
                round_id="round_1",
                issue_code="ISSUE-01",
                title="产品型号在三份资料中不一致",
                description="企业标准中型号为 DSJ80/40/2×75，图纸标题栏标注为 DSJ80/40/75。",
                category_code="cross_file_consistency",
                severity="severe",
                status="open",
                source_file="企业标准.pdf",
                page_no=3,
            ),
            "issue_2": self._issue(
                round_id="round_1",
                issue_code="ISSUE-02",
                title="驱动功率参数低于标准要求",
                description="说明书标注单电机功率 55 kW，与确认标准条款要求及图纸参数不一致。",
                category_code="standard_compliance",
                severity="severe",
                status="confirmed",
                source_file="产品使用说明书.pdf",
                page_no=12,
            ),
        }

        self.reports = {
            "report_1": self._report(
                report_no="SH-2026-000116-REP-V1",
                report_type="正式审核报告",
                product_name="煤矿用移动橡套软电缆",
                product_model="MY-0.38/0.66",
                customer_name="陕西恒工装备有限公司",
                conclusion="通过",
            )
        }

        self.model_configs = {
            "model_1": {
                "id": str(uuid4()),
                "provider_code": "qwen",
                "provider_name": "通义千问",
                "base_url": "https://example.invalid",
                "model_code": "qwen3-vl-plus",
                "model_kind": "multimodal",
                "status": "active",
                "timeout_seconds": 60,
                "concurrency_limit": 4,
                "created_at": _now(),
            }
        }

        self.system_parameters = {
            "param_1": {"param_key": "default_remaining_months", "param_value": 6, "scope": "global"},
            "param_2": {"param_key": "max_task_parallelism", "param_value": 4, "scope": "global"},
        }

    def initialize(self, seed_demo_data: bool = True) -> None:
        del seed_demo_data

    def healthcheck(self) -> bool:
        return True

    def authenticate(self, login_name: str, password: str) -> dict | None:
        if password != self.demo_password:
            return None
        for user in self.users.values():
            if user["login_name"] == login_name and user["status"] == "active":
                return deepcopy(user)
        return None

    def get_user(self, user_id: str) -> dict | None:
        _key, user = self._find_record(self.users, user_id)
        return deepcopy(user) if user else None

    def create_auth_session(self, user_id: str, expires_at: datetime) -> str:
        session_id = str(uuid4())
        with self._lock:
            self.auth_sessions[session_id] = {
                "id": session_id,
                "user_id": user_id,
                "expires_at": expires_at,
                "revoked_at": None,
                "status": "active",
            }
        return session_id

    def is_auth_session_active(self, session_id: str, user_id: str) -> bool:
        with self._lock:
            auth_session = self.auth_sessions.get(session_id)
            return bool(
                auth_session
                and auth_session["user_id"] == user_id
                and auth_session["status"] == "active"
                and auth_session["revoked_at"] is None
                and auth_session["expires_at"] > datetime.now(UTC)
            )

    def revoke_auth_session(self, session_id: str, user_id: str) -> bool:
        with self._lock:
            auth_session = self.auth_sessions.get(session_id)
            if not auth_session or auth_session["user_id"] != user_id or auth_session["status"] != "active":
                return False
            auth_session["status"] = "revoked"
            auth_session["revoked_at"] = datetime.now(UTC)
            return True

    def _find_record(self, collection: dict[str, dict], record_id: str) -> tuple[str, dict] | tuple[None, None]:
        if record_id in collection:
            return record_id, collection[record_id]
        for key, value in collection.items():
            if value.get("id") == record_id:
                return key, value
        return None, None

    def _add_user(self, login_name: str, display_name: str, role: str) -> str:
        user_id = str(uuid4())
        self.users[user_id] = {
            "id": user_id,
            "login_name": login_name,
            "display_name": display_name,
            "role": role,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        return user_id

    def _executor(self, code: str, name: str, kind: str, version: str, status: str, parameter_note: str) -> dict:
        definition_id = str(uuid4())
        executor_version = {
            "id": str(uuid4()),
            "executor_definition_id": definition_id,
            "version_no": version,
            "parameter_schema": {
                **deepcopy(EXECUTOR_PARAMETER_SCHEMAS.get(code, {"type": "object"})),
                "description": parameter_note,
            },
            "result_schema": {"type": "object"},
            "default_timeout_seconds": 60,
            "supports_batch": False,
            "entrypoint": f"coal_platform.executors.{code}:execute",
            "image_version": "worker-demo",
            "status": status,
        }
        return {
            "id": definition_id,
            "executor_code": code,
            "executor_name": name,
            "executor_kind": kind,
            "version_no": version,
            "status": status,
            "input_type": "rule_input",
            "output_type": "rule_result",
            "default_timeout_seconds": 60,
            "supports_batch": False,
            "entrypoint": f"coal_platform.executors.{code}:execute",
            "parameter_note": parameter_note,
            "versions": [executor_version],
        }

    def _standard(self, code: str, name: str, standard_type: str, status: str) -> dict:
        standard_id = str(uuid4())
        version_id = str(uuid4())
        revision = {
            "id": str(uuid4()),
            "revision_no": "P1",
            "status": "published",
            "impact_flag": "no_impact",
            "published_at": _now(),
            "clauses": [
                _clause_payload({"clause_code": "5.3.2", "title": "驱动功率配置", "constraint_level": "必须"}),
                _clause_payload({"clause_code": "附录A", "title": "受控件类别", "constraint_level": "待确认"}),
            ],
        }
        self.standard_parse_revisions[version_id] = [revision]
        return {
            "id": standard_id,
            "standard_code": code,
            "standard_name": name,
            "standard_type": standard_type,
            "status": status,
            "versions": [
                {
                    "id": version_id,
                    "standard_id": standard_id,
                    "full_code": code,
                    "version_label": code.split("-")[-1],
                    "publish_date": "2023-12-20",
                    "implement_date": "2024-07-01",
                    "status": status,
                    "latest_parse_revision": deepcopy(revision),
                }
            ],
        }

    def _rule(self, code: str, name: str, executor_code: str, severity: str) -> dict:
        rule_id = str(uuid4())
        executor = self.executors[executor_code]
        version = {
            "id": str(uuid4()),
            "rule_definition_id": rule_id,
            "version_no": "v1.3",
            "executor_version_id": executor["versions"][0]["id"],
            "executor_code": executor_code,
            "parameters": {},
            "scope_files": [],
            "priority": 100,
            "stage_code": DEFAULT_RULE_STAGE_BY_CODE.get(code, "standard_compliance"),
            "dependency_rule_codes": [],
            "task_override_allowed": True,
            "status": "published",
        }
        return {
            "id": rule_id,
            "rule_code": code,
            "rule_name": name,
            "rule_type": "deterministic",
            "executor_definition_id": executor["id"],
            "executor_code": executor_code,
            "default_issue_category": "technical_compliance",
            "default_severity": severity,
            "version_no": "v1.3",
            "status": "published",
            "severity": severity,
            "is_mandatory": False,
            "affects_suggested_conclusion": True,
            "versions": [version],
        }

    def _task(
        self,
        *,
        task_no: str,
        customer_name: str,
        product_name: str,
        product_model: str,
        owner_user_id: str,
        owner_user_name: str,
        status: str,
        current_round_no: int,
        current_round_id: str,
        final_conclusion: str | None,
        round_note: str,
    ) -> dict:
        task_id = str(uuid4())
        return {
            "id": task_id,
            "task_no": task_no,
            "customer_name": customer_name,
            "product_name": product_name,
            "product_model": product_model,
            "owner_user_id": owner_user_id,
            "owner_user_name": owner_user_name,
            "status": status,
            "current_round_no": current_round_no,
            "current_round_id": current_round_id,
            "final_conclusion": final_conclusion,
            "round_note": round_note,
            "files": [
                {
                    "id": str(uuid4()),
                    "file_name": "产品使用说明书.pdf",
                    "file_type": "user_manual",
                    "status": "parsed",
                    "version_no": 1,
                    "page_count": 46,
                }
            ],
            "rounds": [
                {
                    "id": current_round_id,
                    "round_no": current_round_no,
                    "status": status,
                    "suggested_conclusion": "through" if status == "published" else None,
                    "manual_conclusion": final_conclusion,
                    "standards": ["MT/T 820-2023", "GB/T 10595-2017"],
                }
            ],
            "issues": ["issue_1", "issue_2"],
            "created_at": _now(),
            "updated_at": _now(),
        }

    def _issue(
        self,
        *,
        round_id: str,
        issue_code: str,
        title: str,
        description: str,
        category_code: str,
        severity: str,
        status: str,
        source_file: str,
        page_no: int,
    ) -> dict:
        return {
            "id": str(uuid4()),
            "round_id": round_id,
            "issue_code": issue_code,
            "title": title,
            "description": description,
            "category_code": category_code,
            "severity": severity,
            "status": status,
            "source_file": source_file,
            "page_no": page_no,
            "customer_evidence": {
                "file_name": source_file,
                "page_no": page_no,
                "excerpt_text": description,
            },
            "standard_evidence": {
                "standard_code": "MT/T 820-2023",
                "clause_code": "5.3.2",
                "excerpt_text": "驱动装置额定功率应满足设计输送能力，并与产品型号标示一致。",
            },
            "created_at": _now(),
        }

    def _report(
        self,
        *,
        report_no: str,
        report_type: str,
        product_name: str,
        product_model: str,
        customer_name: str,
        conclusion: str,
    ) -> dict:
        return {
            "id": str(uuid4()),
            "report_no": report_no,
            "report_type": report_type,
            "product_name": product_name,
            "product_model": product_model,
            "customer_name": customer_name,
            "conclusion": conclusion,
            "version_no": 1,
            "status": "published",
            "published_at": _now(),
            "word_object_key": f"reports/{report_no}.docx",
            "pdf_object_key": f"reports/{report_no}.pdf",
        }

    def current_user(self, user_id: str | None = None) -> dict:
        if user_id:
            user = self.get_user(user_id)
            if user:
                return user
        for user in self.users.values():
            if user["login_name"] == "liming":
                return deepcopy(user)
        return deepcopy(next(iter(self.users.values())))

    def list_users(self) -> list[dict]:
        return [deepcopy(item) for item in self.users.values()]

    def list_tasks(self, owner_user_id: str | None = None) -> list[dict]:
        items = self.tasks.values()
        if owner_user_id:
            items = [item for item in items if item["owner_user_id"] == owner_user_id]
        return [deepcopy(item) for item in items]

    def get_task(self, task_id: str) -> dict | None:
        _key, task = self._find_record(self.tasks, task_id)
        return deepcopy(task) if task else None

    def create_task(self, payload: dict) -> dict:
        with self._lock:
            task_id = str(uuid4())
            round_id = str(uuid4())
            task_no = f"SH-2026-{len(self.tasks) + 129:06d}"
            task = {
                "id": task_id,
                "task_no": task_no,
                "customer_name": payload.get("customer_name") or "待确认客户",
                "product_name": payload.get("product_name") or "待确认产品",
                "product_model": payload.get("product_model") or "待确认型号",
                "owner_user_id": payload.get("owner_user_id") or self.current_user()["id"],
                "owner_user_name": self.current_user()["display_name"],
                "status": "draft",
                "current_round_no": 1,
                "current_round_id": round_id,
                "final_conclusion": None,
                "round_note": payload.get("round_note") or "",
                "files": [],
                "rounds": [
                    {
                        "id": round_id,
                        "round_no": 1,
                        "status": "draft",
                        "round_note": payload.get("round_note") or "",
                        "standards": [],
                        "created_at": _now(),
                    }
                ],
                "issues": [],
                "created_at": _now(),
                "updated_at": _now(),
            }
            self.tasks[task_id] = task
            return deepcopy(task)

    def update_task_basic_info(self, task_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, task = self._find_record(self.tasks, task_id)
            if not task:
                return None
            for key in ("customer_name", "product_name", "product_model"):
                if payload.get(key):
                    task[key] = payload[key]
            task["updated_at"] = _now()
            return deepcopy(task)

    def add_task_files(self, task_id: str, files: list[dict]) -> list[dict] | None:
        with self._lock:
            _key, task = self._find_record(self.tasks, task_id)
            if not task:
                return None
            created = []
            for item in files:
                record = {
                    "id": str(uuid4()),
                    "file_name": item["file_name"],
                    "file_type": item.get("file_type") or "other",
                    "content_type": item.get("content_type"),
                    "file_size": item.get("file_size", 0),
                    "sha256": item.get("sha256"),
                    "storage_key": item["storage_key"],
                    "status": "uploaded",
                    "version_no": 1,
                }
                created.append(record)
            task["files"].extend(created)
            task["updated_at"] = _now()
            return deepcopy(created)

    def list_standards(self) -> list[dict]:
        return [deepcopy(item) for item in self.standards.values()]

    def get_standard(self, standard_id: str) -> dict | None:
        _key, standard = self._find_record(self.standards, standard_id)
        return deepcopy(standard) if standard else None

    def create_standard(self, payload: dict) -> dict | None:
        with self._lock:
            if any(item["standard_code"] == payload["standard_code"] for item in self.standards.values()):
                return None
            standard_id = str(uuid4())
            standard = {
                "id": standard_id,
                "standard_code": payload["standard_code"],
                "standard_name": payload["standard_name"],
                "standard_type": payload["standard_type"],
                "scope_text": payload.get("scope_text"),
                "status": "draft",
                "versions": [],
            }
            self.standards[standard_id] = standard
            return deepcopy(standard)

    def list_standard_versions(self, standard_id: str) -> list[dict]:
        standard = self.get_standard(standard_id)
        return standard.get("versions", []) if standard else []

    def get_standard_version(self, version_id: str) -> dict | None:
        standard, version = self._find_standard_version(version_id)
        if version:
            item = deepcopy(version)
            item["standard_code"] = standard["standard_code"]
            item["standard_name"] = standard["standard_name"]
            return item
        return None

    def _find_standard_version(self, version_id: str) -> tuple[dict | None, dict | None]:
        for standard in self.standards.values():
            for version in standard.get("versions", []):
                if version.get("id") == version_id:
                    return standard, version
        return None, None

    def create_standard_version(self, standard_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, standard = self._find_record(self.standards, standard_id)
            if not standard:
                return None
            full_code = payload.get("full_code") or f"{standard['standard_code']}-{payload['version_label']}"
            if any(
                version.get("full_code") == full_code
                for catalog_item in self.standards.values()
                for version in catalog_item.get("versions", [])
            ):
                return None
            version = {
                "id": str(uuid4()),
                "standard_id": standard["id"],
                "full_code": full_code,
                "version_label": payload["version_label"],
                "publish_date": _json_date(payload.get("publish_date")),
                "implement_date": _json_date(payload.get("implement_date")),
                "abolish_date": _json_date(payload.get("abolish_date")),
                "publisher": payload.get("publisher"),
                "mandatory_flag": payload.get("mandatory_flag", False),
                "status": payload.get("status", "draft"),
                "latest_parse_revision": {
                    "id": str(uuid4()),
                    "revision_no": "P1",
                    "status": "draft",
                    "impact_flag": "no_impact",
                    "published_at": None,
                    "clauses": [],
                },
            }
            standard.setdefault("versions", []).append(version)
            self.standard_parse_revisions[version["id"]] = [version["latest_parse_revision"]]
            return deepcopy(version)

    def list_standard_parse_revisions(self, version_id: str) -> list[dict] | None:
        if not self.get_standard_version(version_id):
            return None
        return deepcopy(self.standard_parse_revisions.get(version_id, []))

    def create_standard_parse_revision(self, version_id: str, payload: dict) -> dict | None:
        with self._lock:
            _standard, version = self._find_standard_version(version_id)
            if not version:
                return None
            revisions = self.standard_parse_revisions.setdefault(version_id, [])
            revision = {
                "id": str(uuid4()),
                "revision_no": f"P{len(revisions) + 1}",
                "status": "draft",
                "impact_flag": payload.get("impact_flag", "no_impact"),
                "published_at": None,
                "clauses": deepcopy(
                    [
                        _clause_payload(item)
                        for item in (
                            payload["clauses"]
                            if payload.get("clauses") is not None
                            else version.get("latest_parse_revision", {}).get("clauses", [])
                        )
                    ]
                ),
            }
            revisions.append(revision)
            version["latest_parse_revision"] = deepcopy(revision)
            return deepcopy(revision)

    def publish_standard_parse_revision(self, revision_id: str, payload: dict) -> dict | None:
        with self._lock:
            for version_id, revisions in self.standard_parse_revisions.items():
                selected = next((item for item in revisions if item.get("id") == revision_id), None)
                if not selected:
                    continue
                for revision in revisions:
                    if revision is not selected and revision.get("status") == "published":
                        revision["status"] = "archived"
                selected["status"] = "published"
                selected["published_at"] = _now()
                _standard, version = self._find_standard_version(version_id)
                if version:
                    version["latest_parse_revision"] = deepcopy(selected)
                    version["status"] = "active"
                return deepcopy(selected)
        return None

    def publish_standard_version(self, version_id: str, payload: dict) -> dict | None:
        with self._lock:
            standard, version = self._find_standard_version(version_id)
            if not version:
                return None
            revisions = self.standard_parse_revisions.setdefault(version_id, [])
            if revisions:
                for revision in revisions:
                    if revision.get("status") == "published":
                        revision["status"] = "archived"
                revisions[-1]["status"] = "published"
                revisions[-1]["published_at"] = _now()
                version["latest_parse_revision"] = deepcopy(revisions[-1])
            version["status"] = "active"
            standard["status"] = "有效"
            return deepcopy(version)

    def abolish_standard_version(self, version_id: str, payload: dict) -> dict | None:
        with self._lock:
            standard, version = self._find_standard_version(version_id)
            if not version:
                return None
            successor_id = payload.get("superseded_by_version_id")
            if successor_id and not self.get_standard_version(successor_id):
                return None
            version["status"] = "obsolete"
            version["abolish_date"] = _json_date(payload.get("abolish_date")) or datetime.now(UTC).date().isoformat()
            version["superseded_by_id"] = successor_id
            if not any(item.get("status") == "active" for item in standard.get("versions", [])):
                standard["status"] = "obsolete"
            return deepcopy(version)

    def compare_standard_versions(self, version_id: str, other_version_id: str) -> dict | None:
        left = self.get_standard_version(version_id)
        right = self.get_standard_version(other_version_id)
        if not left or not right:
            return None
        comparison = compare_clause_sets(
            left.get("latest_parse_revision", {}).get("clauses", []),
            right.get("latest_parse_revision", {}).get("clauses", []),
        )
        return {
            "left_version": {"id": left["id"], "full_code": left["full_code"]},
            "right_version": {"id": right["id"], "full_code": right["full_code"]},
            **comparison,
        }

    def list_standard_clauses(self, version_id: str) -> list[dict] | None:
        version = self.get_standard_version(version_id)
        if not version:
            return None
        return deepcopy(version.get("latest_parse_revision", {}).get("clauses", []))

    def list_round_standards(self, round_id: str) -> list[dict] | None:
        for task in self.tasks.values():
            for round_item in task.get("rounds", []):
                if round_item.get("id") != round_id:
                    continue
                result = []
                for item in round_item.get("standards", []):
                    if isinstance(item, str):
                        result.append({"id": None, "round_id": round_id, "standard_code": item, "status": "confirmed"})
                    else:
                        result.append(deepcopy(item))
                return result
        return None

    def list_rules(self) -> list[dict]:
        return [deepcopy(item) for item in self.rules.values()]

    def get_rule(self, rule_id: str) -> dict | None:
        _key, rule = self._find_record(self.rules, rule_id)
        return deepcopy(rule) if rule else None

    def create_rule(self, payload: dict) -> dict | None:
        with self._lock:
            if any(item.get("rule_code") == payload["rule_code"] for item in self.rules.values()):
                return None
            executor = next(
                (item for item in self.executors.values() if item.get("executor_code") == payload["executor_code"]),
                None,
            )
            if not executor:
                return None
            rule_id = str(uuid4())
            self.rules[rule_id] = {
                "id": rule_id,
                "rule_code": payload["rule_code"],
                "rule_name": payload["rule_name"],
                "rule_type": payload["rule_type"],
                "executor_definition_id": executor["id"],
                "executor_code": executor["executor_code"],
                "default_issue_category": payload["default_issue_category"],
                "default_severity": payload["default_severity"],
                "severity": payload["default_severity"],
                "affects_suggested_conclusion": payload.get("affects_suggested_conclusion", False),
                "is_mandatory": payload.get("is_mandatory", False),
                "status": "draft",
                "versions": [],
            }
            return deepcopy(self.rules[rule_id])

    def list_rule_versions(self, rule_id: str) -> list[dict] | None:
        rule = self.get_rule(rule_id)
        return rule.get("versions", []) if rule else None

    def get_rule_version(self, version_id: str) -> dict | None:
        for rule in self.rules.values():
            for version in rule.get("versions", []):
                if version.get("id") == version_id:
                    item = deepcopy(version)
                    item["rule_code"] = rule["rule_code"]
                    item["rule_name"] = rule["rule_name"]
                    item["executor_code"] = rule["executor_code"]
                    return item
        return None

    def create_rule_version(self, rule_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, rule = self._find_record(self.rules, rule_id)
            if not rule:
                return None
            stage_code = payload.get("stage_code", "standard_compliance")
            if errors := validate_stage_code(stage_code):
                raise RuleConfigurationError(errors)
            executor = self.executors.get(rule["executor_code"])
            if not executor:
                return None
            executor_version_id = payload.get("executor_version_id")
            executor_version = next(
                (item for item in executor.get("versions", []) if item.get("id") == executor_version_id),
                None,
            ) if executor_version_id else next(
                (item for item in reversed(executor.get("versions", [])) if item.get("status") == "published"),
                None,
            )
            if not executor_version:
                return None
            version_no = payload.get("version_no") or next_version_no(
                [item["version_no"] for item in rule.get("versions", [])]
            )
            if any(item.get("version_no") == version_no for item in rule.get("versions", [])):
                return None
            version = {
                "id": str(uuid4()),
                "rule_definition_id": rule["id"],
                "version_no": version_no,
                "executor_version_id": executor_version["id"],
                "executor_code": rule["executor_code"],
                "parameters": payload.get("parameters") or {},
                "scope_files": payload.get("scope_files") or [],
                "priority": payload.get("priority", 100),
                "stage_code": payload.get("stage_code", "standard_compliance"),
                "dependency_rule_codes": payload.get("dependency_rule_codes") or [],
                "task_override_allowed": payload.get("task_override_allowed", True),
                "status": "draft",
            }
            rule.setdefault("versions", []).append(version)
            return deepcopy(version)

    def _rule_validation_errors(self, version: dict) -> list[dict]:
        rule = next(
            (item for item in self.rules.values() if item["id"] == version["rule_definition_id"]),
            None,
        )
        if not rule:
            return [{"code": "RULE_NOT_FOUND", "message": "rule definition not found", "path": "rule_definition_id"}]
        executor = self.executors.get(rule["executor_code"])
        executor_version = next(
            (item for item in (executor or {}).get("versions", []) if item["id"] == version["executor_version_id"]),
            None,
        )
        errors = validate_stage_code(version["stage_code"])
        if not executor_version or executor_version.get("status") != "published":
            errors.append({"code": "EXECUTOR_VERSION_UNAVAILABLE", "message": "executor version is not published", "path": "executor_version_id"})
        else:
            errors.extend(validate_parameters(version.get("parameters") or {}, executor_version.get("parameter_schema") or {}))
        graph = {}
        known_rule_codes = set()
        for item in self.rules.values():
            published = next((candidate for candidate in item.get("versions", []) if candidate.get("status") == "published"), None)
            if published:
                graph[item["rule_code"]] = list(published.get("dependency_rule_codes") or [])
                known_rule_codes.add(item["rule_code"])
        graph[rule["rule_code"]] = list(version.get("dependency_rule_codes") or [])
        known_rule_codes.add(rule["rule_code"])
        errors.extend(validate_dependency_graph(graph, known_rule_codes))
        return errors

    def validate_rule_version(self, version_id: str) -> dict | None:
        version = self.get_rule_version(version_id)
        if not version:
            return None
        errors = self._rule_validation_errors(version)
        return {"valid": not errors, "rule_version_id": version_id, "errors": errors}

    def publish_rule_version(self, version_id: str, payload: dict) -> dict | None:
        with self._lock:
            for rule in self.rules.values():
                for version in rule.get("versions", []):
                    if version.get("id") != version_id:
                        continue
                    if errors := self._rule_validation_errors(version):
                        raise RuleConfigurationError(errors)
                    for previous in rule["versions"]:
                        if previous is not version and previous.get("status") == "published":
                            previous["status"] = "archived"
                    version["status"] = "published"
                    rule["status"] = "published"
                    rule["version_no"] = version["version_no"]
                    return deepcopy(version)
        return None

    def list_executors(self) -> list[dict]:
        return [deepcopy(item) for item in self.executors.values()]

    def list_executor_versions(self, executor_code: str) -> list[dict] | None:
        executor = self.executors.get(executor_code)
        return deepcopy(executor.get("versions", [])) if executor else None

    @staticmethod
    def _rule_pack_dict(pack: dict) -> dict:
        item = deepcopy(pack)
        item["source_type"] = item.get("trigger_condition", {}).get("source_type", "global")
        return item

    def list_rule_packs(self) -> list[dict]:
        packs = sorted(
            self.rule_packs.values(),
            key=lambda item: (FIXED_AUDIT_STAGE_ORDER[item["stage_code"]], item["pack_code"]),
        )
        return [self._rule_pack_dict(item) for item in packs]

    def _rule_versions_by_id(self) -> dict[str, dict]:
        return {
            version["id"]: {**deepcopy(version), "rule_code": rule["rule_code"], "rule_name": rule["rule_name"], "is_mandatory": rule.get("is_mandatory", False)}
            for rule in self.rules.values()
            for version in rule.get("versions", [])
        }

    def _validate_rule_pack_payload(self, payload: dict, member_ids: list[str]) -> list[dict]:
        errors = validate_stage_code(payload.get("stage_code", ""))
        errors.extend(validate_trigger_condition(payload.get("trigger_condition") or {}))
        if payload.get("status", "draft") not in {"draft", "published", "disabled", "archived"}:
            errors.append({"code": "INVALID_PACK_STATUS", "message": "unknown rule pack status", "path": "status"})
        if payload.get("status") == "published" and not member_ids:
            errors.append({"code": "EMPTY_RULE_PACK", "message": "published rule pack must contain rules", "path": "rule_version_ids"})
        versions = self._rule_versions_by_id()
        for index, version_id in enumerate(member_ids):
            version = versions.get(version_id)
            if not version:
                errors.append({"code": "RULE_VERSION_NOT_FOUND", "message": "rule version not found", "path": f"rule_version_ids.{index}"})
                continue
            if version.get("status") != "published":
                errors.append({"code": "RULE_VERSION_NOT_PUBLISHED", "message": "rule pack only accepts published rule versions", "path": f"rule_version_ids.{index}"})
            if version.get("stage_code") != payload.get("stage_code"):
                errors.append({"code": "STAGE_MISMATCH", "message": "rule version stage does not match rule pack stage", "path": f"rule_version_ids.{index}"})
        return errors

    def create_rule_pack(self, payload: dict) -> dict | None:
        with self._lock:
            if any(item.get("pack_code") == payload["pack_code"] for item in self.rule_packs.values()):
                return None
            member_ids = payload.get("rule_version_ids") or []
            if errors := self._validate_rule_pack_payload(payload, member_ids):
                raise RuleConfigurationError(errors)
            pack_id = str(uuid4())
            versions = self._rule_versions_by_id()
            self.rule_packs[pack_id] = {
                "id": pack_id,
                "pack_code": payload["pack_code"],
                "pack_name": payload["pack_name"],
                "stage_code": payload["stage_code"],
                "trigger_condition": deepcopy(payload.get("trigger_condition") or {}),
                "status": payload.get("status", "draft"),
                "items": [
                    {
                        "id": str(uuid4()),
                        "rule_pack_id": pack_id,
                        "rule_version_id": version_id,
                        "rule_code": versions[version_id]["rule_code"],
                        "version_no": versions[version_id]["version_no"],
                        "order_no": index + 1,
                        "enabled": True,
                    }
                    for index, version_id in enumerate(member_ids)
                ],
            }
            return self._rule_pack_dict(self.rule_packs[pack_id])

    def update_rule_pack(self, pack_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, pack = self._find_record(self.rule_packs, pack_id)
            if not pack:
                return None
            updated = deepcopy(pack)
            for key in ("pack_name", "stage_code", "trigger_condition", "status"):
                if payload.get(key) is not None:
                    updated[key] = payload[key]
            member_ids = payload.get("rule_version_ids")
            if member_ids is None:
                member_ids = [item["rule_version_id"] for item in updated.get("items", [])]
            if errors := self._validate_rule_pack_payload(updated, member_ids):
                raise RuleConfigurationError(errors)
            versions = self._rule_versions_by_id()
            updated["items"] = [
                {
                    "id": str(uuid4()),
                    "rule_pack_id": updated["id"],
                    "rule_version_id": version_id,
                    "rule_code": versions[version_id]["rule_code"],
                    "version_no": versions[version_id]["version_no"],
                    "order_no": index + 1,
                    "enabled": True,
                }
                for index, version_id in enumerate(member_ids)
            ]
            pack.clear()
            pack.update(updated)
            return self._rule_pack_dict(pack)

    def _find_round(self, round_id: str) -> tuple[dict | None, dict | None]:
        for task in self.tasks.values():
            for round_item in task.get("rounds", []):
                if round_item.get("id") == round_id:
                    return task, round_item
        return None, None

    def list_round_rules(self, round_id: str) -> list[dict] | None:
        _task, round_item = self._find_round(round_id)
        return deepcopy(round_item.get("rules", [])) if round_item else None

    def assemble_round_rules(self, round_id: str, payload: dict) -> dict | None:
        with self._lock:
            task, round_item = self._find_round(round_id)
            if not task or not round_item:
                return None
            if round_item.get("rules"):
                rules = deepcopy(round_item["rules"])
                return {"round_id": round_id, "snapshot_no": rules[0]["snapshot_no"], "locked": True, "rules": rules}
            selected_pack_ids = set(payload.get("rule_pack_ids") or [])
            missing_pack_ids = selected_pack_ids - set(self.rule_packs)
            if missing_pack_ids:
                raise RuleConfigurationError(
                    [
                        {"code": "RULE_PACK_NOT_FOUND", "message": f"rule pack not found: {pack_id}", "path": "rule_pack_ids"}
                        for pack_id in sorted(missing_pack_ids)
                    ]
                )
            packs = [item for item in self.rule_packs.values() if item.get("status") == "published"]
            if selected_pack_ids:
                packs = [item for item in packs if item["id"] in selected_pack_ids]
            file_types = [item.get("file_type", "other") for item in task.get("files", [])]
            confirmed_standard_count = sum(1 for item in round_item.get("standards", []) if isinstance(item, dict) and item.get("status") == "confirmed")
            candidate_rules = []
            skipped_packs = []
            versions = self._rule_versions_by_id()
            for pack in packs:
                enabled, reason = evaluate_trigger_condition(
                    pack.get("trigger_condition") or {}, file_types=file_types, confirmed_standard_count=confirmed_standard_count
                )
                if not enabled:
                    skipped_packs.append({"pack_code": pack["pack_code"], "reason": reason})
                    continue
                for member in sorted(pack.get("items", []), key=lambda item: item["order_no"]):
                    if not member.get("enabled") or member["rule_version_id"] in {
                        item["id"] for item in candidate_rules
                    }:
                        continue
                    version = versions.get(member["rule_version_id"])
                    if not version:
                        continue
                    candidate_rules.append({**version, "source_type": pack.get("trigger_condition", {}).get("source_type", "global"), "enable_reason": reason, "pack_code": pack["pack_code"]})
            candidate_rules.sort(key=lambda item: (FIXED_AUDIT_STAGE_ORDER[item["stage_code"]], item["priority"], item["rule_code"]))
            snapshot_no = f"RULE-SNAPSHOT-R{round_item.get('round_no', 1)}-{round_id[:8]}"
            rules = [
                {
                    "id": str(uuid4()),
                    "round_id": round_id,
                    "rule_version_id": item["id"],
                    "executor_version_id": item["executor_version_id"],
                    "rule_code": item["rule_code"],
                    "rule_name": item["rule_name"],
                    "executor_code": item["executor_code"],
                    "stage_code": item["stage_code"],
                    "source_type": item["source_type"],
                    "enable_reason": item["enable_reason"],
                    "enabled": True,
                    "override_payload": None,
                    "disable_reason": None,
                    "snapshot_no": snapshot_no,
                }
                for item in candidate_rules
            ]
            round_item["rules"] = rules
            round_item["rule_snapshot_no"] = snapshot_no
            return {"round_id": round_id, "snapshot_no": snapshot_no, "locked": True, "rules": deepcopy(rules), "skipped_packs": skipped_packs}

    def list_reports(self) -> list[dict]:
        return [deepcopy(item) for item in self.reports.values()]

    def list_issues(self, round_id: str | None = None) -> list[dict]:
        items = self.issues.values()
        if round_id:
            items = [item for item in items if item["round_id"] == round_id]
        return [deepcopy(item) for item in items]

    def get_issue(self, issue_id: str) -> dict | None:
        item = self.issues.get(issue_id)
        return deepcopy(item) if item else None

    def list_model_configs(self) -> list[dict]:
        return [deepcopy(item) for item in self.model_configs.values()]

    def list_system_parameters(self) -> list[dict]:
        return [deepcopy(item) for item in self.system_parameters.values()]

    def list_operation_logs(self) -> list[dict]:
        return []

    def create_round(self, task_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, task = self._find_record(self.tasks, task_id)
            if not task:
                return None
            round_no = task["current_round_no"] + 1
            round_id = str(uuid4())
            previous_round = task["rounds"][-1] if task["rounds"] else None
            inherited_rules = []
            if payload.get("inherit_previous_snapshot", True) and previous_round:
                snapshot_no = f"RULE-SNAPSHOT-R{round_no}-{round_id[:8]}"
                inherited_rules = [
                    {
                        **deepcopy(item),
                        "id": str(uuid4()),
                        "round_id": round_id,
                        "snapshot_no": snapshot_no,
                        "enable_reason": "沿用上一轮规则和执行器版本",
                    }
                    for item in previous_round.get("rules", [])
                ]
            new_round = {
                "id": round_id,
                "round_no": round_no,
                "status": "draft",
                "round_note": payload.get("round_note") or "",
                "inherit_previous_snapshot": payload.get("inherit_previous_snapshot", True),
                "standards": list(task["rounds"][-1]["standards"]) if task["rounds"] else [],
                "rules": inherited_rules,
                "created_at": _now(),
            }
            task["current_round_no"] = round_no
            task["current_round_id"] = round_id
            task["status"] = "in_new_round"
            task["rounds"].append(new_round)
            task["updated_at"] = _now()
            return deepcopy(new_round)

    def get_round(self, round_id: str) -> dict | None:
        for task in self.tasks.values():
            for item in task.get("rounds", []):
                if item.get("id") == round_id:
                    round_item = deepcopy(item)
                    round_item["task_id"] = task["id"]
                    return round_item
        return None

    def start_audit(self, round_id: str, payload: dict) -> dict | None:
        with self._lock:
            matching_task = None
            matching_round = None
            for task in self.tasks.values():
                for item in task.get("rounds", []):
                    if item.get("id") == round_id:
                        matching_task = task
                        matching_round = item
                        break
                if matching_task:
                    break
            if not matching_task or not matching_round:
                return None

            active_run = next(
                (
                    run
                    for run in self.audit_runs.values()
                    if run["round_id"] == round_id and run["status"] in {"queued", "running"}
                ),
                None,
            )
            if active_run:
                return deepcopy(active_run)

            run_id = str(uuid4())
            run = {
                "id": run_id,
                "audit_run_id": run_id,
                "task_id": matching_task["id"],
                "round_id": round_id,
                "run_no": sum(1 for item in self.audit_runs.values() if item["round_id"] == round_id) + 1,
                "status": "queued",
                "job_id": str(uuid4()),
                "job_status": "queued",
                "created_at": _now(),
            }
            self.audit_runs[run_id] = run
            for round_rule in matching_round.get("rules", []):
                if not round_rule.get("enabled", True):
                    continue
                input_snapshot = {
                    "round_id": round_id,
                    "rule_snapshot_no": round_rule.get("snapshot_no"),
                    "rule_version_id": round_rule["rule_version_id"],
                }
                input_hash = sha256(json.dumps(input_snapshot, sort_keys=True).encode()).hexdigest()
                execution_id = str(uuid4())
                self.rule_executions[execution_id] = {
                    "id": execution_id,
                    "audit_run_id": run_id,
                    "round_id": round_id,
                    "rule_version_id": round_rule["rule_version_id"],
                    "rule_code": round_rule.get("rule_code"),
                    "executor_version_id": round_rule["executor_version_id"],
                    "status": "pending",
                    "input_snapshot": input_snapshot,
                    "normalized_input_hash": input_hash,
                    "retry_count": 0,
                    "attempt_count": 0,
                    "attempts": [],
                    "created_at": _now(),
                }
            matching_task["status"] = "auditing"
            matching_task["updated_at"] = _now()
            matching_round["status"] = "auditing"
            matching_round["updated_at"] = _now()
            return deepcopy(run)

    def local_rerun(self, round_id: str, payload: dict) -> dict | None:
        with self._lock:
            matching_task = None
            matching_round = None
            for task in self.tasks.values():
                for item in task.get("rounds", []):
                    if item.get("id") == round_id:
                        matching_task, matching_round = task, item
                        break
                if matching_round:
                    break
            if not matching_task or not matching_round:
                return None
            affected = set(payload.get("affected_rule_codes") or [])
            rules = [item for item in matching_round.get("rules", []) if item.get("rule_code") in affected]
            if affected - {item.get("rule_code") for item in rules}:
                raise ValueError("rule is not present in round snapshot")
            if not rules:
                raise ValueError("no affected rule found in round snapshot")
            for execution in self.rule_executions.values():
                if execution["round_id"] == round_id and execution.get("rule_code") in affected:
                    execution["is_expired"] = True
            impact_id = str(uuid4())
            impact = {
                "id": impact_id, "round_id": round_id, "trigger_type": "local_rerun",
                "trigger_payload": {"reason": payload["reason"], "input_change": payload.get("input_change", {})},
                "affected_rule_codes": sorted(affected), "status": "queued", "created_at": _now(),
            }
            self.impact_analyses[impact_id] = impact
            run_id = str(uuid4())
            run = {
                "id": run_id, "audit_run_id": run_id, "task_id": matching_task["id"],
                "round_id": round_id, "run_no": sum(1 for item in self.audit_runs.values() if item["round_id"] == round_id) + 1,
                "status": "queued", "job_id": str(uuid4()), "job_status": "queued", "created_at": _now(),
                "impact_analysis_id": impact_id, "run_scope": "local",
            }
            self.audit_runs[run_id] = run
            for round_rule in rules:
                input_snapshot = {
                    "round_id": round_id, "rule_snapshot_no": round_rule.get("snapshot_no"),
                    "rule_version_id": round_rule["rule_version_id"], "input_change": payload.get("input_change", {}),
                }
                execution_id = str(uuid4())
                self.rule_executions[execution_id] = {
                    "id": execution_id, "audit_run_id": run_id, "round_id": round_id,
                    "rule_version_id": round_rule["rule_version_id"], "rule_code": round_rule.get("rule_code"),
                    "executor_version_id": round_rule["executor_version_id"], "status": "pending",
                    "input_snapshot": input_snapshot,
                    "normalized_input_hash": sha256(json.dumps(input_snapshot, sort_keys=True).encode()).hexdigest(),
                    "retry_count": 0, "attempt_count": 0, "attempts": [], "is_expired": False, "created_at": _now(),
                }
            run["affected_rule_codes"] = sorted(affected)
            return deepcopy(run)

    def list_audit_runs(self, round_id: str) -> list[dict] | None:
        if not self.get_round(round_id):
            return None
        return [deepcopy(item) for item in self.audit_runs.values() if item["round_id"] == round_id]

    def list_rule_executions(self, round_id: str) -> list[dict] | None:
        if not self.get_round(round_id):
            return None
        return [deepcopy(item) for item in self.rule_executions.values() if item["round_id"] == round_id]

    def get_rule_execution(self, execution_id: str) -> dict | None:
        item = self.rule_executions.get(execution_id)
        return deepcopy(item) if item else None

    def list_execution_attempts(self, execution_id: str) -> list[dict] | None:
        item = self.rule_executions.get(execution_id)
        return deepcopy(item.get("attempts", [])) if item else None

    def record_execution_attempt(self, execution_id: str, payload: dict) -> dict | None:
        with self._lock:
            item = self.rule_executions.get(execution_id)
            if not item:
                return None
            status = payload.get("status", "succeeded")
            if status not in {"running", "succeeded", "failed", "unable_to_determine", "exception", "canceled", "expired"}:
                raise ValueError("invalid execution attempt status")
            attempt = {
                "id": str(uuid4()),
                "rule_execution_id": execution_id,
                "attempt_no": len(item["attempts"]) + 1,
                "attempt_kind": payload.get("attempt_kind", "normal"),
                "executor_version_id": item["executor_version_id"],
                "status": status,
                "input_payload": deepcopy(payload.get("input_payload") or item["input_snapshot"]),
                "output_payload": deepcopy(payload.get("output_payload")),
                "error_payload": deepcopy(payload.get("error_payload")),
                "elapsed_ms": payload.get("elapsed_ms"),
                "created_at": _now(),
            }
            item["attempts"].append(attempt)
            item["attempt_count"] = len(item["attempts"])
            item["status"] = status
            item["result_payload"] = deepcopy(payload.get("output_payload"))
            item["elapsed_ms"] = payload.get("elapsed_ms")
            item["updated_at"] = _now()
            issue_payload = (payload.get("output_payload") or {}).get("issue")
            if issue_payload:
                issue_code = issue_payload.get("issue_code") or f"EXEC-{execution_id[:8]}"
                issue = next(
                    (
                        candidate for candidate in self.issues.values()
                        if candidate["round_id"] == item["round_id"] and candidate["issue_code"] == issue_code
                    ),
                    None,
                )
                incoming_severity = issue_payload.get("severity") or (issue.get("severity") if issue else "一般")
                incoming_conclusion = issue_payload.get("system_conclusion", "failed")
                is_conflict = bool(
                    issue
                    and (
                        issue.get("severity") != incoming_severity
                        or issue.get("system_conclusion") != incoming_conclusion
                    )
                )
                if not issue:
                    issue_id = str(uuid4())
                    issue = {
                        "id": issue_id,
                        "round_id": item["round_id"],
                        "issue_code": issue_code,
                        "title": issue_payload.get("title", "审核问题"),
                        "description": issue_payload.get("description", "规则执行发现不符合项"),
                        "category_code": issue_payload.get("category_code", "technical_compliance"),
                        "severity": issue_payload.get("severity", "一般"),
                        "status": "open",
                        "system_conclusion": issue_payload.get("system_conclusion", "failed"),
                        "manual_conclusion": None,
                        "affects_conclusion": issue_payload.get("affects_conclusion", False),
                        "manual_reason": None,
                        "sources": [],
                        "evidence": [],
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                    self.issues[issue_id] = issue
                customer_evidence = issue_payload.get("customer_evidence") or []
                standard_evidence = issue_payload.get("standard_evidence") or []
                if isinstance(customer_evidence, dict):
                    customer_evidence = [customer_evidence]
                if isinstance(standard_evidence, dict):
                    standard_evidence = [standard_evidence]
                source_status = "active"
                if not customer_evidence or not standard_evidence:
                    source_status = "evidence_insufficient"
                    issue["system_conclusion"] = "unable_to_determine"
                if is_conflict:
                    source_status = "conflict"
                    issue["system_conclusion"] = "conflict_requires_review"
                    issue["status"] = "open"
                    for source in issue["sources"]:
                        source["source_status"] = "conflict"
                if not any(source["rule_execution_id"] == execution_id for source in issue["sources"]):
                    issue["sources"].append({
                        "source_type": "rule_execution",
                        "rule_execution_id": execution_id,
                        "source_status": source_status,
                        "source_payload": deepcopy(issue_payload),
                    })
                    issue["evidence"].extend(
                        {"evidence_type": evidence_type, **deepcopy(entry)}
                        for evidence_type, entries in (("customer", customer_evidence), ("standard", standard_evidence))
                        for entry in entries
                    )
            return deepcopy(item)

    def retry_rule_execution(self, execution_id: str, payload: dict) -> dict | None:
        with self._lock:
            item = self.rule_executions.get(execution_id)
            if not item:
                return None
            if item["status"] in {"running", "pending"}:
                raise ValueError("execution is already queued or running")
            item["retry_count"] += 1
            item["status"] = "pending"
            item["updated_at"] = _now()
            return deepcopy(item)

    def update_issue(self, issue_id: str, payload: dict) -> dict | None:
        with self._lock:
            _key, issue = self._find_record(self.issues, issue_id)
            if not issue:
                return None
            for key in ("title", "description", "category_code", "severity", "affects_conclusion"):
                if payload.get(key) is not None:
                    issue[key] = payload[key]
            if payload.get("reason"):
                issue["manual_reason"] = payload["reason"]
            issue["updated_at"] = _now()
            return deepcopy(issue)

    def set_issue_status(
        self, issue_id: str, status: str, reason: str | None = None, context: dict | None = None
    ) -> dict | None:
        with self._lock:
            _key, issue = self._find_record(self.issues, issue_id)
            if not issue:
                return None
            issue["status"] = status
            if reason:
                issue["manual_reason"] = reason
            issue["updated_at"] = _now()
            return deepcopy(issue)

    def add_standard_to_round(self, round_id: str, payload: dict) -> dict | None:
        round_item = None
        for task in self.tasks.values():
            for candidate in task.get("rounds", []):
                if candidate.get("id") == round_id:
                    round_item = candidate
                    break
            if round_item:
                break
        if not round_item:
            return None
        version = self.get_standard_version(payload.get("standard_version_id"))
        if not version:
            return None
        item = {
            "id": str(uuid4()),
            "round_id": round_id,
            "standard_version_id": payload.get("standard_version_id") or str(uuid4()),
            "standard_code": version["full_code"],
            "standard_name": version["standard_name"],
            "source_type": payload.get("source_type", "document_reference"),
            "status": "selected",
            "skip_reason": None,
        }
        round_item.setdefault("standards", []).append(item)
        return deepcopy(item)

    def confirm_round_standard(self, round_id: str, round_standard_id: str, payload: dict) -> dict | None:
        standards = self.list_round_standards(round_id)
        if standards is None:
            return None
        for item in standards:
            if item.get("id") == round_standard_id:
                item["status"] = "confirmed"
                item["snapshot_no"] = f"R-{round_id[:8]}"
                for task in self.tasks.values():
                    for round_item in task.get("rounds", []):
                        if round_item.get("id") == round_id:
                            for stored in round_item.get("standards", []):
                                if isinstance(stored, dict) and stored.get("id") == round_standard_id:
                                    stored.update(item)
                            return deepcopy(item)
        return None

    def list_dynamic_items(self, round_id: str) -> list[dict] | None:
        _task, round_item = self._find_round(round_id)
        if not round_item:
            return None
        items = []
        for standard in round_item.get("standards", []):
            if standard.get("status") != "confirmed":
                continue
            version = self.get_standard_version(standard.get("standard_version_id")) or {}
            for clause in version.get("latest_parse_revision", {}).get("clauses", []):
                items.append({
                    "id": f"dynamic-{round_id[:8]}-{clause['id'][:8]}",
                    "round_id": round_id,
                    "source_clause": f"{standard.get('standard_code')} {clause.get('clause_code')}",
                    "source_clause_id": clause.get("id"),
                    "subject_code": clause.get("clause_code"),
                    "subject_name": clause.get("title") or clause.get("clause_code"),
                    "applicability_status": "to_confirm",
                    "execution_mode": "deterministic",
                })
        return items

    def list_coverage(self, round_id: str) -> dict | None:
        items = self.list_dynamic_items(round_id)
        if items is None:
            return None
        return {"round_id": round_id, "summary": {"to_confirm": len(items)}, "items": items}
