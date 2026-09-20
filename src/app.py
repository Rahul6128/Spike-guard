"""AWS Lambda entry point behind an API Gateway HTTP API.

Routes
  GET  /                    dashboard
  GET  /api/health          liveness check
  GET  /api/summary         everything the dashboard shows
  POST /api/ingest          score a real 5-minute window of payments
  POST /api/simulate        score one synthetic scenario (demo)
  POST /api/seed            load an hour of synthetic demo traffic
  POST /api/alerts/{id}     reviewer decision: {"action": "confirm" | "dismiss"}
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import traceback
from decimal import Decimal

import pipeline
import simulator
import store

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "dashboard.html"), encoding="utf-8") as fh:
    DASHBOARD_HTML = fh.read()

ALERT_ID = re.compile(r"^\d{13}-[0-9a-f]{6}$")
MERCHANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TXNS = 2000


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(type(o).__name__)


def _resp(status, body, content_type="application/json; charset=utf-8", cache="no-store"):
    if not isinstance(body, str):
        body = json.dumps(body, default=_json_default)
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type, "Cache-Control": cache,
                    "X-Content-Type-Options": "nosniff"},
        "body": body,
    }


def _body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "Body must be valid JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "Body must be a JSON object.")
    return data


def _summary():
    stats = store.get_stats()
    reviewed = stats["confirmed"] + stats["dismissed"]
    return {
        "stats": {
            "windows_scored": stats["windows_scored"],
            "alerts_raised": stats["alerts_raised"],
            "alerts_open": max(0, stats["alerts_raised"] - reviewed),
            "confirmed": stats["confirmed"],
            "dismissed": stats["dismissed"],
            "exposure": stats["exposure"],
            "precision": (stats["confirmed"] / reviewed) if reviewed else None,
        },
        "alerts": store.recent_alerts(30),
        "windows": store.recent_windows(90),
        "threshold": pipeline.threshold(),
        "region": os.environ.get("AWS_REGION", ""),
        "merchants": [{"id": m["id"], "name": m["name"]} for m in simulator.MERCHANTS],
        "server_time": int(time.time()),
    }


def _ingest(event):
    key = os.environ.get("INGEST_API_KEY", "")
    if key:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-api-key") != key:
            raise ApiError(401, "Missing or wrong x-api-key header.")
    data = _body(event)
    merchant_id = str(data.get("merchant_id", ""))
    if not MERCHANT_ID.match(merchant_id):
        raise ApiError(400, "merchant_id is required (letters, digits, - or _, up to 64 chars).")
    txns = data.get("transactions")
    if not isinstance(txns, list) or not (1 <= len(txns) <= MAX_TXNS):
        raise ApiError(400, "transactions must be a list of 1 to {} payments.".format(MAX_TXNS))
    clean = []
    for t in txns:
        if not isinstance(t, dict) or not isinstance(t.get("amount"), (int, float)):
            raise ApiError(400, "Every transaction needs a numeric 'amount'.")
        clean.append(t)
    name = str(data.get("merchant_name") or merchant_id)[:80]
    end_ts = data.get("window_end")
    end_ts = int(end_ts) if isinstance(end_ts, (int, float)) and end_ts > 0 else int(time.time())
    return pipeline.ingest(merchant_id, name, clean, end_ts)


def _simulate(event):
    data = _body(event)
    scenario = data.get("scenario")
    if scenario not in simulator.SCENARIOS:
        raise ApiError(400, "scenario must be one of: " + ", ".join(simulator.SCENARIOS))
    merchant_id = data.get("merchant_id")
    if merchant_id and not simulator.by_id(merchant_id):
        raise ApiError(400, "Unknown demo merchant.")
    return pipeline.simulate(scenario, merchant_id)


def _review(event, alert_id):
    if not ALERT_ID.match(alert_id):
        raise ApiError(400, "Bad alert id.")
    action = _body(event).get("action")
    if action not in ("confirm", "dismiss"):
        raise ApiError(400, "action must be 'confirm' or 'dismiss'.")
    alert = store.review_alert(alert_id, action)
    if alert is None:
        raise ApiError(409, "That alert was already reviewed, or does not exist.")
    return alert


def handler(event, context=None):
    try:
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET").upper()
        path = event.get("rawPath", "/") or "/"
        if len(path) > 1:
            path = path.rstrip("/")

        if method == "GET" and path == "/":
            return _resp(200, DASHBOARD_HTML, "text/html; charset=utf-8", cache="no-cache")
        if method == "GET" and path == "/api/health":
            return _resp(200, {"ok": True, "region": os.environ.get("AWS_REGION", "")})
        if method == "GET" and path == "/api/summary":
            return _resp(200, _summary())
        if method == "POST" and path == "/api/ingest":
            return _resp(200, _ingest(event))
        if method == "POST" and path == "/api/simulate":
            return _resp(200, _simulate(event))
        if method == "POST" and path == "/api/seed":
            return _resp(200, pipeline.seed_demo())
        if method == "POST" and path.startswith("/api/alerts/"):
            return _resp(200, _review(event, path.rsplit("/", 1)[1]))
        return _resp(404, {"error": "Not found."})
    except ApiError as exc:
        return _resp(exc.status, {"error": exc.message})
    except Exception:  # noqa: BLE001
        print(json.dumps({"level": "error", "trace": traceback.format_exc()}))
        return _resp(500, {"error": "Something went wrong on the server. Check the CloudWatch logs."})
