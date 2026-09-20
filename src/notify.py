"""Alert delivery (Amazon SNS) and metrics (CloudWatch Embedded Metric Format)."""
from __future__ import annotations

import json
import os
import time

import boto3

_sns = None


def _client():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns")
    return _sns


def reset_cache():
    global _sns
    _sns = None


def publish_alert(alert):
    """Email/SMS the on-call reviewer. Never lets a delivery problem fail the request."""
    topic = os.environ.get("TOPIC_ARN")
    if not topic:
        return False
    subject = "[SpikeGuard] {} alert: {} at {}".format(
        str(alert["severity"]).upper(), alert["pattern_label"], alert["merchant_name"])
    subject = subject.encode("ascii", "ignore").decode()[:100]
    lines = [
        "{} at {}".format(alert["pattern_label"], alert["merchant_name"]),
        "Severity: {}   Score: {}".format(alert["severity"], alert["score"]),
        "Payments in window: {}   Approved value: Rs {:,.0f}".format(alert["n"], alert["exposure"]),
        "", "Why it was flagged:",
    ]
    lines += ["  - " + r for r in alert["reasons"]]
    lines += ["", "Suggested next step: " + alert["action"],
              "", "Open the dashboard to confirm it as fraud or dismiss it as a false alarm."]
    try:
        _client().publish(TopicArn=topic, Subject=subject, Message="\n".join(lines))
        return True
    except Exception as exc:  # noqa: BLE001 - alert delivery is best-effort
        print(json.dumps({"level": "warn", "msg": "sns publish failed", "error": str(exc)}))
        return False


def emit_metrics(result, merchant_id):
    """Write CloudWatch custom metrics by logging one Embedded Metric Format line."""
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "SpikeGuard",
                "Dimensions": [["Pattern"]],
                "Metrics": [
                    {"Name": "WindowsScored", "Unit": "Count"},
                    {"Name": "AlertsRaised", "Unit": "Count"},
                    {"Name": "WindowScore", "Unit": "None"},
                ],
            }],
        },
        "Pattern": result["pattern"] or "none",
        "WindowsScored": 1,
        "AlertsRaised": 1 if result["flagged"] else 0,
        "WindowScore": result["score"],
        "merchant_id": merchant_id,
    }
    print(json.dumps(doc))
