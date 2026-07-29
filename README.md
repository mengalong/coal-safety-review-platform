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

另开终端启动 React 生产前端开发服务：

```bash
cd frontend
npm ci
npm run dev
```

前端地址：`http://127.0.0.1:5173`。Vite 会将 `/api` 请求代理到本机 `8000` 端口。

开发环境会幂等创建两个演示账号：

| 登录名 | 角色 | 密码 |
|---|---|---|
| `liming` | 审核人员 | `coal123456` |
| `admin` | 管理员 | `coal123456` |

演示密码只用于本地开发。业务接口需要先调用 `POST /api/v1/auth/login` 获取 Bearer Token。
默认运行时使用 SQLite 和 `./data/uploads` 本地对象目录，任务、轮次和文件在 API
重启后仍然保留。设置 `COAL_STORE_BACKEND=demo` 可临时切回纯内存演示数据。
本机默认关闭 OCR；安装 Tesseract 及简体中文语言包后，设置
`COAL_OCR_BACKEND=tesseract` 可启用扫描 PDF 的 CPU OCR。
百度千帆模型通过 `COAL_QIANFAN_API_KEY` 注入；模型凭据使用 `COAL_MODEL_SECRET_KEY` 加密后入库。
生产环境必须分别使用随机强密钥，并通过密钥管理系统注入，不能提交 `.env`。

原型通过真实登录、当前用户、任务列表和退出接口连接本地 API。先启动 API，再启动原型静态服务：

```bash
python -m http.server 65513 --directory prototype
```

原型地址：`http://127.0.0.1:65513`

## 容器启动

```bash
docker compose up --build
```

该命令会启动 React/Nginx Web、API、Celery Worker、PostgreSQL/pgvector、Redis 和 MinIO。容器镜像内置 Tesseract、简体中文和英文识别数据，不依赖 GPU。API 会将文档解析、审核和试运行作业自动派发到 Redis，Worker 从 Redis 消费作业，失败作业最多自动重试 3 次。生产前端地址为 `http://127.0.0.1:8080`，MinIO 控制台地址为 `http://127.0.0.1:9001`。

## 验证

```bash
pytest
ruff check coal_platform tests
alembic upgrade head
alembic downgrade base
cd frontend && npm run lint && npm run test && npm run build
docker compose config
```

默认 `.env.example` 使用 SQLite，便于只运行 API 骨架；容器环境使用 PostgreSQL。
