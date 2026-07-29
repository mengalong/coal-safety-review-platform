from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def notification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    alerts = payload.get("alerts") or []
    return {
        "status": payload.get("status"),
        "alert_count": len(alerts),
        "alerts": [
            {
                "status": item.get("status"),
                "name": (item.get("labels") or {}).get("alertname"),
                "severity": (item.get("labels") or {}).get("severity"),
                "summary": (item.get("annotations") or {}).get("summary"),
                "starts_at": item.get("startsAt"),
            }
            for item in alerts[:50]
        ],
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/alerts")
def forward_alerts(payload: dict[str, Any]) -> dict[str, Any]:
    target = os.getenv("COAL_ALERT_WEBHOOK_URL")
    if not target:
        raise HTTPException(status_code=503, detail="alert webhook is not configured")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("COAL_ALERT_WEBHOOK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(target, json=notification_payload(payload), headers=headers, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="alert delivery failed") from exc
    return {"delivered": True, "alert_count": len(payload.get("alerts") or [])}
