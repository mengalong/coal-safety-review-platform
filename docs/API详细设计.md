# API 详细设计

## 1. 设计约定

1. 所有 API 采用 REST 风格，前端以轮询为主。
2. 资源主键统一使用 UUID；可读编号单独返回。
3. 所有修改接口都要求角色校验和对象归属校验。
4. 所有状态变化都要落 `operation_log`。
5. 长耗时任务返回作业 ID，立即响应 `202 Accepted`。
6. 除健康检查、登录和 OpenAPI 文档外，请求使用 `Authorization: Bearer <token>`。
7. 审核人员只能访问本人负责的任务；管理员可以跨负责人查询并维护管理资源。

## 2. 通用响应

### 2.1 成功响应

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "trace_id": "7f3a..."
}
```

### 2.2 分页响应

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 128
}
```

### 2.3 错误响应

```json
{
  "code": "TASK_NOT_FOUND",
  "message": "任务不存在",
  "detail": {}
}
```

## 3. 认证与用户

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/auth/login` | 登录 |
| `POST` | `/api/v1/auth/logout` | 退出 |
| `GET` | `/api/v1/auth/me` | 当前用户 |
| `PATCH` | `/api/v1/auth/password` | 修改密码 |
| `GET` | `/api/v1/users` | 用户列表 |
| `POST` | `/api/v1/users` | 创建用户 |
| `PATCH` | `/api/v1/users/{user_id}` | 编辑用户 |
| `PATCH` | `/api/v1/users/{user_id}/status` | 启停用户 |

关键字段：

```json
{
  "login_name": "liming",
  "display_name": "李明",
  "role": "reviewer",
  "status": "active"
}
```

认证语义：

1. 登录成功后创建独立 `auth_session`，其 UUID 写入 JWT `jti`。
2. `/auth/me` 和所有受保护接口都校验对应会话仍为 `active` 且未过期。
3. `/auth/logout` 只撤销当前 `jti` 对应会话；同一用户的其他有效登录不受影响。
4. 已退出令牌再次访问受保护接口返回 `401 UNAUTHORIZED`。
5. 修改密码请求包含 `current_password` 和 `new_password`，新密码长度为 10 至 128 个字符，且不得与当前密码相同。
6. 修改密码成功后撤销该用户全部有效 `auth_session`，包括发起请求的当前会话；用户须使用新密码重新登录。
7. 密码及密码哈希不得写入操作日志；数据库仅记录 `user.password.change` 和撤销会话数量。

## 4. 任务与文件

### 4.1 任务列表与详情

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/tasks` | 任务列表 |
| `POST` | `/api/v1/tasks` | 上传资料创建任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 任务详情 |
| `PATCH` | `/api/v1/tasks/{task_id}/basic-info` | 确认基本信息 |
| `POST` | `/api/v1/tasks/{task_id}/transfer` | 转交任务 |
| `POST` | `/api/v1/tasks/{task_id}/void` | 作废任务 |

任务列表支持查询参数：

`task_no`, `customer_name`, `product_name`, `product_model`, `owner_user_id`, `status`, `round_no`, `page`, `page_size`, `sort`.

返回示例：

```json
{
  "items": [
    {
      "id": "uuid",
      "task_no": "SH-2026-000128",
      "customer_name": "晋北装备制造有限公司",
      "product_name": "带式输送机",
      "product_model": "DSJ80/40/2x75",
      "status": "waiting_review",
      "owner_user_name": "李明"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

### 4.2 文件接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/tasks/{task_id}/files` | 上传文件 |
| `PATCH` | `/api/v1/tasks/{task_id}/files/{file_id}` | 修正文件类型 |
| `PUT` | `/api/v1/tasks/{task_id}/files/{file_id}` | 替换文件并递增文件版本 |
| `DELETE` | `/api/v1/tasks/{task_id}/files/{file_id}` | 软删除文件并保留审计记录 |
| `POST` | `/api/v1/tasks/{task_id}/files/{file_id}/retry-parse` | 重试解析 |
| `POST` | `/api/v1/tasks/{task_id}/files/{file_id}/mark-unavailable` | 标记无法解析继续 |

上传响应：

```json
{
  "job_id": "uuid",
  "file_id": "uuid",
  "status": "uploaded"
}
```

## 5. 轮次与标准

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/tasks/{task_id}/rounds` | 轮次列表 |
| `POST` | `/api/v1/tasks/{task_id}/rounds` | 创建新轮次 |
| `GET` | `/api/v1/rounds/{round_id}` | 轮次详情 |
| `POST` | `/api/v1/rounds/{round_id}/standards` | 添加适用标准 |
| `DELETE` | `/api/v1/rounds/{round_id}/standards/{round_standard_id}` | 移除标准 |
| `POST` | `/api/v1/rounds/{round_id}/standards/{round_standard_id}/confirm` | 确认标准快照 |
| `POST` | `/api/v1/rounds/{round_id}/standards/upload-temp` | 上传临时标准 |
| `GET` | `/api/v1/rounds/{round_id}/standards` | 本轮标准列表 |

轮次创建请求：

```json
{
  "round_note": "客户补充整改文件",
  "inherit_previous_snapshot": true
}
```

## 6. 标准库

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/standards` | 标准列表 |
| `POST` | `/api/v1/standards` | 创建标准主档 |
| `POST` | `/api/v1/standards/{standard_id}/versions` | 新增版本 |
| `GET` | `/api/v1/standards/{standard_id}` | 标准详情 |
| `GET` | `/api/v1/standards/{standard_id}/versions` | 版本列表 |
| `GET` | `/api/v1/standard-versions/{standard_version_id}` | 版本详情 |
| `POST` | `/api/v1/standard-versions/{standard_version_id}/publish` | 发布版本 |
| `POST` | `/api/v1/standard-versions/{standard_version_id}/parse-revisions` | 新增解析修订 |
| `GET` | `/api/v1/standard-versions/{standard_version_id}/clauses` | 条款列表 |
| `GET` | `/api/v1/standard-versions/{standard_version_id}/compare/{other_version_id}` | 版本比较 |
| `GET` | `/api/v1/standards/search` | 检索 |

标准版本详情需返回：

`full_code`, `standard_name`, `publish_date`, `implement_date`, `status`, `latest_parse_revision`, `relation_summary`, `task_usage_count`.

## 7. 执行器与规则

### 7.1 执行器

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/executors` | 执行器目录 |
| `GET` | `/api/v1/executors/{executor_code}` | 执行器详情 |
| `GET` | `/api/v1/executors/{executor_code}/versions` | 版本列表 |
| `POST` | `/api/v1/executors/{executor_version_id}/pause` | 暂停 |
| `POST` | `/api/v1/executors/{executor_version_id}/ban` | 封禁 |
| `POST` | `/api/v1/executors/{executor_version_id}/restore` | 恢复 |
| `POST` | `/api/v1/executors/{executor_version_id}/deprecate` | 弃用 |

### 7.2 规则

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/rules` | 规则列表 |
| `POST` | `/api/v1/rules` | 创建规则主档 |
| `GET` | `/api/v1/rules/{rule_id}` | 规则详情 |
| `POST` | `/api/v1/rules/{rule_id}/versions` | 创建新版本 |
| `GET` | `/api/v1/rules/{rule_id}/versions` | 规则版本列表 |
| `GET` | `/api/v1/rule-versions/{rule_version_id}` | 规则版本详情 |
| `POST` | `/api/v1/rule-versions/{rule_version_id}/validate` | 执行发布前配置校验 |
| `POST` | `/api/v1/rule-versions/{rule_version_id}/publish` | 发布版本 |
| `POST` | `/api/v1/rule-versions/{rule_version_id}/disable` | 停用版本 |
| `POST` | `/api/v1/rule-versions/{rule_version_id}/copy` | 复制为新草稿版本 |
| `POST` | `/api/v1/rule-versions/{rule_version_id}/test-runs` | 试运行 |
| `GET` | `/api/v1/rule-packs` | 规则包列表 |
| `POST` | `/api/v1/rule-packs` | 创建规则包 |
| `PATCH` | `/api/v1/rule-packs/{pack_id}` | 编辑规则包 |
| `GET` | `/api/v1/settings/audit-stages` | 固定审核阶段列表 |

规则发布前校验：

1. 参数 JSON Schema 通过。
2. 执行器版本存在且白名单可用。
3. 不存在依赖循环。
4. 试运行无未处置执行异常。
5. AI 规则满足结构化输出约束。

第一期规则包只能选择系统固定审核阶段，成员必须引用同阶段的已发布规则版本。触发条件支持全局基础规则、最少文件数、任一或全部文件类型以及已确认标准要求，不开放任意脚本表达式。

### 7.3 本轮规则快照

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/rounds/{round_id}/rules` | 查询本轮规则与执行器版本快照 |
| `POST` | `/api/v1/rounds/{round_id}/rules/assemble` | 按规则包和任务文件装配并锁定快照 |

首次装配写入 `round_rule` 后，同一轮次重复调用只返回原快照，不重新选择新发布版本。创建新轮次时默认复制上一轮的规则版本、执行器版本、启停状态和任务级覆盖；显式关闭 `inherit_previous_snapshot` 后才允许按最新已发布配置重新装配。

## 8. 动态审核项与覆盖清单

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/rounds/{round_id}/dynamic-items` | 动态审核项列表 |
| `POST` | `/api/v1/rounds/{round_id}/dynamic-items/{item_id}/confirm` | 确认适用 |
| `POST` | `/api/v1/rounds/{round_id}/dynamic-items/{item_id}/exclude` | 排除 |
| `POST` | `/api/v1/rounds/{round_id}/dynamic-items/{item_id}/manual` | 人工处理 |
| `GET` | `/api/v1/rounds/{round_id}/coverage` | 覆盖清单 |
| `POST` | `/api/v1/rounds/{round_id}/coverage/check` | 发布前完整性检查 |

动态项返回至少包含：

`source_clause`, `subject_name`, `applicability_status`, `execution_mode`, `customer_evidence`, `standard_evidence`, `manual_state`.

## 9. 审核运行与问题

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/rounds/{round_id}/audit/start` | 启动完整审核 |
| `POST` | `/api/v1/rounds/{round_id}/audit/rerun` | 整轮重跑 |
| `POST` | `/api/v1/rounds/{round_id}/audit/local-rerun` | 局部重跑 |
| `POST` | `/api/v1/rule-executions/{execution_id}/rerun` | 单规则重跑 |
| `GET` | `/api/v1/rounds/{round_id}/audit-runs` | 审核运行列表 |
| `GET` | `/api/v1/rule-executions/{execution_id}` | 执行详情 |
| `GET` | `/api/v1/rule-executions/{execution_id}/attempts` | 尝试记录 |
| `GET` | `/api/v1/issues` | 问题列表 |
| `POST` | `/api/v1/rounds/{round_id}/issues` | 人工新增问题并挂接证据 |
| `PATCH` | `/api/v1/issues/{issue_id}` | 修改问题 |
| `POST` | `/api/v1/issues/{issue_id}/confirm` | 确认问题 |
| `POST` | `/api/v1/issues/{issue_id}/reject` | 驳回问题 |
| `POST` | `/api/v1/issues/{issue_id}/close` | 关闭问题 |
| `POST` | `/api/v1/issues` | 人工新增问题 |

问题修改请求示例：

```json
{
  "title": "驱动功率参数低于标准要求",
  "description": "说明书标注单电机功率 55 kW，与标准和图纸参数不一致。",
  "category_code": "standard_compliance",
  "severity": "severe",
  "reason": "人工复核确认涉及核心参数"
}
```

## 10. 报告

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/reports` | 报告列表 |
| `GET` | `/api/v1/reports/{report_id}` | 报告详情 |
| `GET` | `/api/v1/reports/{report_id}/preview` | 报告内容快照预览 |
| `POST` | `/api/v1/rounds/{round_id}/reports` | 生成报告 |
| `POST` | `/api/v1/reports/{report_id}/publish` | 发布报告 |
| `GET` | `/api/v1/reports/{report_id}/artifacts` | 报告文件清单 |
| `GET` | `/api/v1/reports/{report_id}/artifacts/{artifact_type}/download` | 下载 Word/PDF 文件 |

报告详情应返回：

`report_no`, `report_type`, `conclusion`, `word_object_key`, `pdf_object_key`, `standard_snapshot`, `rule_snapshot`, `issue_summary`.

## 11. 系统管理与监控

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/settings/models` | 模型配置 |
| `POST` | `/api/v1/settings/models` | 新增模型配置 |
| `PATCH` | `/api/v1/settings/models/{config_id}` | 更新模型配置状态和运行参数 |
| `GET` | `/api/v1/settings/issue-categories` | 问题分类 |
| `POST` | `/api/v1/settings/issue-categories` | 新增或更新问题分类 |
| `GET` | `/api/v1/settings/report-templates` | 报告模板 |
| `POST` | `/api/v1/settings/report-templates` | 新增或更新报告模板 |
| `GET` | `/api/v1/settings/system-parameters` | 系统参数 |
| `PUT` | `/api/v1/settings/system-parameters/{param_key}` | 新增或更新系统参数 |
| `GET` | `/api/v1/jobs` | 异步作业 |
| `POST` | `/api/v1/jobs/{job_id}/run` | 管理员触发待执行作业 |
| `POST` | `/api/v1/jobs/{job_id}/retry` | 失败作业重新入队 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 取消作业 |
| `GET` | `/api/v1/monitoring` | 队列和 Worker 指标 |
| `GET` | `/api/v1/monitoring/alerts` | 系统告警列表 |
| `GET` | `/api/v1/logs` | 操作日志 |

作业控制语义：

1. 仅管理员可运行、重试和取消作业。
2. 只有 `queued`、`pending` 状态的作业可以取消，成功后状态变为 `canceled` 并写入 `finished_at`。
3. 已取消或已经开始、结束的作业再次取消返回 `409 CONFLICT`；不存在的作业返回 `404 NOT_FOUND`。
4. `canceled` 是终态，Worker、手工运行和失败重试均不得再次执行该作业。
5. 数据库存储在取消时锁定作业记录，并写入 `queue_job.cancel` 操作日志及前后状态快照。

## 12. 重要状态机

### 12.1 任务主状态

`draft -> parsing -> waiting_basic_info -> waiting_standards -> auditing -> waiting_review -> waiting_publish -> published -> waiting_rectification -> in_new_round -> completed`

### 12.2 规则执行状态

`pending -> waiting_dependency -> running -> passed / failed / unable_to_determine / exception / canceled / expired`

### 12.3 覆盖状态

`executed_passed`, `executed_failed`, `missing_data`, `unable_to_determine`, `not_applicable`, `to_confirm`, `unsupported`, `execution_exception`

## 13. 首期实现范围

首期先实现：

1. 登录、当前用户、任务列表、任务详情。
2. 文件上传、轮次创建、标准确认。
3. 标准库列表、版本详情、条款查询。
4. 执行器目录、规则列表、规则版本。
5. 审核运行占位、问题列表、报告列表。
6. 系统参数和监控读接口。
7. 代码、日志、命名和多架构镜像构建规范见《工程规范》。
