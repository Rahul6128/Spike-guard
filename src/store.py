"""DynamoDB access for SpikeGuard (single table, on-demand billing).

Key design (pk / sk):
  MERCHANT#<id> / STATE        rolling baseline history + region footprint
  WINDOW        / <ts>#<id>#x  every scored 5-minute window (auto-expires after 3 days)
  ALERT         / <alert id>   flagged windows waiting for (or done with) human review
  STATS         / TOTAL        atomic counters shown on the dashboard
"""
from __future__ import annotations

import json
import os
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

_table = None
WINDOW_TTL_SECONDS = 3 * 24 * 3600


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def reset_cache():
    global _table
    _table = None


def to_dynamo(obj):
    """DynamoDB stores numbers as Decimal, not float."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_dynamo(obj):
    if isinstance(obj, list):
        return [from_dynamo(x) for x in obj]
    if isinstance(obj, dict):
        return {k: from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


# ---- merchants ---------------------------------------------------------------

def get_merchant(merchant_id):
    item = table().get_item(Key={"pk": "MERCHANT#" + merchant_id, "sk": "STATE"}).get("Item")
    if not item:
        return {"history": [], "footprint": {}}
    item = from_dynamo(item)
    return {"history": item.get("history", []), "footprint": item.get("footprint", {})}


def put_merchant(merchant_id, state, name=None):
    item = {
        "pk": "MERCHANT#" + merchant_id, "sk": "STATE",
        "history": state["history"], "footprint": state["footprint"],
        "updated": int(time.time()),
    }
    if name:
        item["name"] = name
    table().put_item(Item=to_dynamo(item))


# ---- windows -----------------------------------------------------------------

def window_item(merchant_id, merchant_name, end_ts, result):
    return {
        "pk": "WINDOW",
        "sk": "{:013d}#{}#{}".format(int(end_ts), merchant_id, uuid.uuid4().hex[:6]),
        "ts": int(end_ts), "merchant_id": merchant_id, "merchant_name": merchant_name,
        "score": result["score"], "flagged": result["flagged"],
        "pattern_label": result["pattern_label"] or "", "n": int(result["features"]["n"]),
        "ttl": int(time.time()) + WINDOW_TTL_SECONDS,
    }


def put_window(item):
    table().put_item(Item=to_dynamo(item))


def batch_put(items):
    with table().batch_writer() as bw:
        for it in items:
            bw.put_item(Item=to_dynamo(it))


def recent_windows(limit=90):
    r = table().query(KeyConditionExpression=Key("pk").eq("WINDOW"),
                      ScanIndexForward=False, Limit=limit)
    return list(reversed(from_dynamo(r.get("Items", []))))


# ---- alerts ------------------------------------------------------------------

def new_alert_id(ts):
    return "{:013d}-{}".format(int(ts * 1000), uuid.uuid4().hex[:6])


def alert_item(merchant_id, merchant_name, end_ts, result):
    return {
        "pk": "ALERT", "sk": new_alert_id(end_ts), "status": "open",
        "merchant_id": merchant_id, "merchant_name": merchant_name, "ts": int(end_ts),
        "score": result["score"], "severity": result["severity"],
        "pattern": result["pattern"], "pattern_label": result["pattern_label"],
        "reasons": result["reasons"], "action": result["action"],
        "exposure": round(result["features"].get("approved_amount", 0.0), 2),
        "n": int(result["features"]["n"]),
    }


def put_alert(item):
    table().put_item(Item=to_dynamo(item))


def recent_alerts(limit=30):
    r = table().query(KeyConditionExpression=Key("pk").eq("ALERT"),
                      ScanIndexForward=False, Limit=limit)
    out = []
    for it in from_dynamo(r.get("Items", [])):
        it["id"] = it.pop("sk")
        it.pop("pk", None)
        out.append(it)
    return out


def review_alert(alert_id, action):
    """Mark an open alert confirmed / dismissed. Returns the alert, or None if it is not open."""
    status = {"confirm": "confirmed", "dismiss": "dismissed"}[action]
    try:
        r = table().update_item(
            Key={"pk": "ALERT", "sk": alert_id},
            UpdateExpression="SET #s = :s, reviewed_at = :t",
            ConditionExpression="attribute_exists(pk) AND #s = :open",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":t": int(time.time()), ":open": "open"},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    incr_stats(**{status: 1})
    item = from_dynamo(r["Attributes"])
    item["id"] = item.pop("sk")
    item.pop("pk", None)
    return item


# ---- counters ----------------------------------------------------------------

def incr_stats(**deltas):
    deltas = {k: v for k, v in deltas.items() if v}
    if not deltas:
        return
    names, values, parts = {}, {}, []
    for i, (k, v) in enumerate(deltas.items()):
        names["#k%d" % i] = k
        values[":v%d" % i] = Decimal(str(round(v, 2)))
        parts.append("#k%d :v%d" % (i, i))
    table().update_item(
        Key={"pk": "STATS", "sk": "TOTAL"},
        UpdateExpression="ADD " + ", ".join(parts),
        ExpressionAttributeNames=names, ExpressionAttributeValues=values,
    )


def get_stats():
    item = table().get_item(Key={"pk": "STATS", "sk": "TOTAL"}).get("Item") or {}
    item = from_dynamo(item)
    return {
        "windows_scored": item.get("windows_scored", 0),
        "alerts_raised": item.get("alerts_raised", 0),
        "confirmed": item.get("confirmed", 0),
        "dismissed": item.get("dismissed", 0),
        "exposure": item.get("exposure", 0),
    }
