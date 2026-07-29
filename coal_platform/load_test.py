from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx

Mode = Literal["read", "tasks", "uploads", "mixed"]


@dataclass
class Sample:
    operation: str
    status: int
    latency_ms: float
    error: str | None = None


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize(samples: list[Sample], elapsed: float, queue_peak: int) -> dict:
    latencies = [sample.latency_ms for sample in samples]
    failed = sum(sample.status < 200 or sample.status >= 400 for sample in samples)
    return {
        "requests": len(samples),
        "failed": failed,
        "error_rate": round(failed / len(samples), 4) if samples else 0.0,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(samples) / elapsed, 3) if elapsed else 0.0,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "queue_waiting_peak": queue_peak,
        "operations": dict(sorted({name: sum(item.operation == name for item in samples) for name in {item.operation for item in samples}}.items())),
        "errors": [asdict(sample) for sample in samples if sample.error][:20],
    }


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> tuple[httpx.Response | None, Sample]:
    started = time.monotonic()
    try:
        response = await client.request(method, url, **kwargs)
        error = None if response.is_success else response.text[:300]
        return response, Sample(url, response.status_code, (time.monotonic() - started) * 1000, error)
    except httpx.HTTPError as exc:
        return None, Sample(url, 0, (time.monotonic() - started) * 1000, type(exc).__name__)


async def run_load_test(
    base_url: str,
    login_name: str,
    password: str,
    mode: Mode,
    requests: int,
    concurrency: int,
    file_size_mb: int,
    verify: bool | str = True,
    wait_queue_seconds: int = 0,
) -> dict:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(300, connect=10)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), verify=verify, limits=limits, timeout=timeout) as client:
        login = await client.post("/api/v1/auth/login", json={"login_name": login_name, "password": password})
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        semaphore = asyncio.Semaphore(concurrency)
        stop_sampling = asyncio.Event()
        queue_peaks: list[int] = [0]
        target_size = file_size_mb * 1024 * 1024
        marker = b"Coal safety load test document "
        upload_content = marker + (b"X" * max(0, target_size - len(marker)))

        async def create_task(index: int) -> tuple[httpx.Response | None, Sample]:
            return await _request(
                client,
                "POST",
                "/api/v1/tasks",
                json={
                    "customer_name": "二期容量验收",
                    "product_name": "压力测试样机",
                    "product_model": f"LOAD-{index}-{uuid4().hex[:8]}",
                    "round_note": "自动化压力测试，可按任务前缀清理",
                },
            )

        async def operation(index: int) -> list[Sample]:
            async with semaphore:
                selected = mode if mode != "mixed" else ("read", "tasks", "uploads")[index % 3]
                if selected == "read":
                    _, sample = await _request(client, "GET", "/api/v1/tasks?page=1&page_size=20")
                    sample.operation = "list_tasks"
                    return [sample]
                response, task_sample = await create_task(index)
                task_sample.operation = "create_task"
                if selected == "tasks" or response is None or not response.is_success:
                    return [task_sample]
                task_id = response.json()["data"]["id"]
                _, upload_sample = await _request(
                    client,
                    "POST",
                    f"/api/v1/tasks/{task_id}/files",
                    files={"files": (f"load-{index}.txt", upload_content, "text/plain")},
                )
                upload_sample.operation = "upload_file"
                return [task_sample, upload_sample]

        async def sample_queue() -> None:
            while not stop_sampling.is_set():
                try:
                    response = await client.get("/api/v1/jobs")
                    if response.is_success:
                        jobs = response.json().get("data") or []
                        waiting = sum(item.get("status") in {"queued", "pending"} for item in jobs)
                        queue_peaks[0] = max(queue_peaks[0], waiting)
                except httpx.HTTPError:
                    pass
                try:
                    await asyncio.wait_for(stop_sampling.wait(), timeout=0.5)
                except TimeoutError:
                    continue

        started = time.monotonic()
        sampler = asyncio.create_task(sample_queue())
        batches = await asyncio.gather(*(operation(index) for index in range(requests)))
        drain_started = time.monotonic()
        queue_drained = False
        if wait_queue_seconds:
            deadline = drain_started + wait_queue_seconds
            while time.monotonic() < deadline:
                response = await client.get("/api/v1/jobs")
                response.raise_for_status()
                jobs = response.json().get("data") or []
                waiting = sum(item.get("status") in {"queued", "pending", "running"} for item in jobs)
                queue_peaks[0] = max(queue_peaks[0], waiting)
                if waiting == 0:
                    queue_drained = True
                    break
                await asyncio.sleep(1)
        stop_sampling.set()
        await sampler
        elapsed = time.monotonic() - started
        result = summarize([sample for batch in batches for sample in batch], elapsed, queue_peaks[0])
        result["queue_drained"] = queue_drained if wait_queue_seconds else None
        result["queue_drain_seconds"] = round(time.monotonic() - drain_started, 3) if wait_queue_seconds else None
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coal platform production load and backlog acceptance test")
    parser.add_argument("--base-url", required=True, help="HTTPS base URL, for example https://coal.example.com")
    parser.add_argument("--login-name", required=True, help="Dedicated administrator test account")
    parser.add_argument("--mode", choices=("read", "tasks", "uploads", "mixed"), default="read")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--file-size-mb", type=int, default=20)
    parser.add_argument("--ca-file", help="Internal CA certificate bundle; TLS verification is never disabled")
    parser.add_argument("--wait-queue-seconds", type=int, default=0, help="Wait for queued/running jobs to drain")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=5000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-write", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.file_size_mb < 1 or args.file_size_mb > 49:
        raise SystemExit("requests/concurrency must be positive and file-size-mb must be between 1 and 49")
    if args.mode != "read" and not args.confirm_write:
        raise SystemExit("write load modes require --confirm-write")
    password = os.getenv("COAL_LOAD_TEST_PASSWORD")
    if not password:
        raise SystemExit("COAL_LOAD_TEST_PASSWORD is required")
    result = asyncio.run(
        run_load_test(
            args.base_url,
            args.login_name,
            password,
            args.mode,
            args.requests,
            args.concurrency,
            args.file_size_mb,
            args.ca_file or True,
            args.wait_queue_seconds,
        )
    )
    result["thresholds"] = {"max_error_rate": args.max_error_rate, "max_p95_ms": args.max_p95_ms}
    result["passed"] = (
        result["error_rate"] <= args.max_error_rate
        and result["latency_ms"]["p95"] <= args.max_p95_ms
        and result["queue_drained"] is not False
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
