from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DemoStore:
    demo_password = "coal123456"

    def __init__(self) -> None:
        self._lock = RLock()
        self.users: dict[str, dict] = {}
        self.auth_sessions: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.audit_runs: dict[str, dict] = {}
        self.standards: dict[str, dict] = {}
        self.rules: dict[str, dict] = {}
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
        return {
            "id": str(uuid4()),
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
        }

    def _standard(self, code: str, name: str, standard_type: str, status: str) -> dict:
        standard_id = str(uuid4())
        version_id = str(uuid4())
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
                    "parse_revision": {
                        "id": str(uuid4()),
                        "revision_no": "P1",
                        "status": "published",
                        "impact_flag": "no_impact",
                        "clauses": [
                            {"clause_code": "5.3.2", "title": "驱动功率配置", "constraint_level": "必须"},
                            {"clause_code": "附录A", "title": "受控件类别", "constraint_level": "待确认"},
                        ],
                    },
                }
            ],
        }

    def _rule(self, code: str, name: str, executor_code: str, severity: str) -> dict:
        return {
            "id": str(uuid4()),
            "rule_code": code,
            "rule_name": name,
            "rule_type": "deterministic",
            "executor_code": executor_code,
            "version_no": "v1.3",
            "status": "published",
            "severity": severity,
            "is_mandatory": False,
            "affects_suggested_conclusion": True,
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
                    "content_type": item.get("content_type"),
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

    def list_rules(self) -> list[dict]:
        return [deepcopy(item) for item in self.rules.values()]

    def list_executors(self) -> list[dict]:
        return [deepcopy(item) for item in self.executors.values()]

    def list_reports(self) -> list[dict]:
        return [deepcopy(item) for item in self.reports.values()]

    def list_issues(self, round_id: str | None = None) -> list[dict]:
        items = self.issues.values()
        if round_id:
            items = [item for item in items if item["round_id"] == round_id]
        return [deepcopy(item) for item in items]

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
            new_round = {
                "id": round_id,
                "round_no": round_no,
                "status": "draft",
                "round_note": payload.get("round_note") or "",
                "inherit_previous_snapshot": payload.get("inherit_previous_snapshot", True),
                "standards": list(task["rounds"][-1]["standards"]) if task["rounds"] else [],
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
            matching_task["status"] = "auditing"
            matching_task["updated_at"] = _now()
            matching_round["status"] = "auditing"
            matching_round["updated_at"] = _now()
            return deepcopy(run)

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

    def set_issue_status(self, issue_id: str, status: str, reason: str | None = None) -> dict | None:
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
        item = {
            "id": str(uuid4()),
            "round_id": round_id,
            "standard_version_id": payload.get("standard_version_id") or str(uuid4()),
            "standard_code": payload.get("standard_code"),
            "standard_name": payload.get("standard_name"),
            "source_type": payload.get("source_type", "document_reference"),
            "status": "selected",
            "skip_reason": None,
        }
        round_item.setdefault("standards", []).append(item)
        return deepcopy(item)
