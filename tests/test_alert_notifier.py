from coal_platform.alert_notifier import notification_payload


def test_notification_payload_only_forwards_operational_alert_fields() -> None:
    result = notification_payload(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "QueueBacklog", "severity": "warning", "secret": "not-forwarded"},
                    "annotations": {"summary": "审核队列持续积压", "description": "internal detail"},
                    "startsAt": "2026-07-29T00:00:00Z",
                }
            ],
        }
    )
    assert result["alert_count"] == 1
    assert result["alerts"][0]["name"] == "QueueBacklog"
    assert "secret" not in str(result)
    assert "internal detail" not in str(result)
