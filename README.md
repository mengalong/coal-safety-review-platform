# 煤矿安标技术文档智能审核平台

当前仓库包含平台详细设计、交互原型，以及第一期 FastAPI 后端底座。

## 本机开发

项目使用独立 Conda 环境 `coal`：

```bash
conda activate coal
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn coal_platform.main:app --reload --host 127.0.0.1 --port 8000
```

API 文档：`http://127.0.0.1:8000/api/docs`

开发环境会幂等创建两个演示账号：

| 登录名 | 角色 | 密码 |
|---|---|---|
| `liming` | 审核人员 | `coal123456` |
| `admin` | 管理员 | `coal123456` |

演示密码只用于本地开发。业务接口需要先调用 `POST /api/v1/auth/login` 获取 Bearer Token。
默认运行时使用 SQLite 和 `./data/uploads` 本地对象目录，任务、轮次和文件在 API
重启后仍然保留。设置 `COAL_STORE_BACKEND=demo` 可临时切回纯内存演示数据。

原型通过真实登录、当前用户、任务列表和退出接口连接本地 API。先启动 API，再启动原型静态服务：

```bash
python -m http.server 65513 --directory prototype
```

原型地址：`http://127.0.0.1:65513`

## 容器启动

```bash
docker compose up --build
```

该命令会启动 API、Celery Worker、PostgreSQL/pgvector、Redis 和 MinIO。API 会将审核和试运行作业自动派发到 Redis，Worker 从 Redis 消费作业，失败作业最多自动重试 3 次。MinIO 控制台地址为
`http://127.0.0.1:9001`。

## 验证

```bash
pytest
ruff check coal_platform tests
alembic upgrade head
alembic downgrade base
```

默认 `.env.example` 使用 SQLite，便于只运行 API 骨架；容器环境使用 PostgreSQL。
