"""Runs the real Lambda handler against mocked DynamoDB and SNS (moto)."""
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture()
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("TABLE_NAME", "spikeguard-test")
    monkeypatch.delenv("INGEST_API_KEY", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName="spikeguard-test", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}])
        topic = boto3.client("sns").create_topic(Name="spikeguard-test")["TopicArn"]
        monkeypatch.setenv("TOPIC_ARN", topic)
        import notify
        import store
        store.reset_cache()
        notify.reset_cache()
        import app
        yield app


def call(app, method, path, body=None, headers=None):
    event = {"requestContext": {"http": {"method": method}}, "rawPath": path,
             "headers": headers or {}, "body": json.dumps(body) if body is not None else None}
    r = app.handler(event)
    ctype = r["headers"]["Content-Type"]
    return r["statusCode"], (json.loads(r["body"]) if ctype.startswith("application/json") else r["body"])


def test_dashboard_and_health(aws):
    code, html = call(aws, "GET", "/")
    assert code == 200 and "SpikeGuard" in html
    assert call(aws, "GET", "/api/health")[1]["ok"] is True
    assert call(aws, "GET", "/nope")[0] == 404


def test_seed_then_summary(aws):
    code, res = call(aws, "POST", "/api/seed", {})
    assert code == 200 and res["seeded"] is True and res["windows"] == 84
    assert res["alerts"] >= 4
    _, s = call(aws, "GET", "/api/summary")
    assert s["stats"]["windows_scored"] == 84
    assert s["stats"]["alerts_open"] == res["alerts"]
    assert len(s["windows"]) == 84
    labels = {a["pattern_label"] for a in s["alerts"]}
    assert {"Card testing", "Velocity attack", "Bust-out", "Geographic anomaly"} <= labels
    # the legitimate flash sale in the seed must NOT have produced an alert
    assert not any(a["merchant_id"] == "m-kolkata-sweets" for a in s["alerts"])
    # seeding twice does nothing
    assert call(aws, "POST", "/api/seed", {})[1]["seeded"] is False


def test_simulate_attack_and_review_flow(aws):
    code, v = call(aws, "POST", "/api/simulate",
                   {"scenario": "card_testing", "merchant_id": "m-jaipur-handloom"})
    assert code == 200 and v["flagged"] and v["pattern"] == "card_testing" and v["warmed_up"]
    alert_id = v["alert_id"]
    _, s = call(aws, "GET", "/api/summary")
    assert s["stats"]["alerts_open"] == 1

    code, a = call(aws, "POST", "/api/alerts/" + alert_id, {"action": "confirm"})
    assert code == 200 and a["status"] == "confirmed"
    assert call(aws, "POST", "/api/alerts/" + alert_id, {"action": "dismiss"})[0] == 409
    _, s = call(aws, "GET", "/api/summary")
    assert s["stats"]["confirmed"] == 1 and s["stats"]["alerts_open"] == 0
    assert s["stats"]["precision"] == 1.0


def test_flash_sale_stays_quiet_through_api(aws):
    code, v = call(aws, "POST", "/api/simulate",
                   {"scenario": "flash_sale", "merchant_id": "m-chennai-fresh"})
    assert code == 200 and v["flagged"] is False and v["alert_id"] is None


def test_sns_alert_is_published(aws):
    sns = boto3.client("sns")
    sqs = boto3.client("sqs")
    q = sqs.create_queue(QueueName="capture")["QueueUrl"]
    qarn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=os.environ["TOPIC_ARN"], Protocol="sqs", Endpoint=qarn)
    call(aws, "POST", "/api/simulate", {"scenario": "bust_out", "merchant_id": "m-delhi-electronics"})
    msgs = sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=5).get("Messages", [])
    assert msgs, "expected an SNS message for the flagged window"
    assert "Bust-out" in json.loads(msgs[0]["Body"])["Subject"]


def test_ingest_real_payments(aws):
    txns = [{"amount": 5.0, "status": "declined", "device_id": "bot", "card_bin": str(400000 + i),
             "geo": "IN-Jaipur"} for i in range(80)]
    code, v = call(aws, "POST", "/api/ingest", {"merchant_id": "my-shop", "transactions": txns})
    # first windows of a new merchant are its learning period: scored, stored, never flagged
    assert code == 200 and v["confidence"] == "low" and v["flagged"] is False
    assert "Learning" in v["note"] and 0 <= v["score"] <= 1
    # once the merchant has a baseline, the same kind of burst is flagged
    quiet = [{"amount": 400, "status": "approved", "device_id": "d%d" % (i % 9),
              "card_bin": str(410000 + i % 15), "geo": "IN-Jaipur"} for i in range(30)]
    for _ in range(7):
        call(aws, "POST", "/api/ingest", {"merchant_id": "my-shop", "transactions": quiet})
    code, v = call(aws, "POST", "/api/ingest", {"merchant_id": "my-shop", "transactions": txns})
    assert code == 200 and v["flagged"] is True and v["pattern"] == "card_testing"


def test_ingest_validation_and_api_key(aws, monkeypatch):
    assert call(aws, "POST", "/api/ingest", {"transactions": []})[0] == 400
    assert call(aws, "POST", "/api/ingest", {"merchant_id": "x", "transactions": [{"amount": "ten"}]})[0] == 400
    assert call(aws, "POST", "/api/ingest", {"merchant_id": "bad id!", "transactions": [{"amount": 1}]})[0] == 400
    monkeypatch.setenv("INGEST_API_KEY", "s3cret")
    ok = {"merchant_id": "x", "transactions": [{"amount": 10}]}
    assert call(aws, "POST", "/api/ingest", ok)[0] == 401
    assert call(aws, "POST", "/api/ingest", ok, {"X-Api-Key": "s3cret"})[0] == 200


def test_bad_requests(aws):
    assert call(aws, "POST", "/api/simulate", {"scenario": "nope"})[0] == 400
    assert call(aws, "POST", "/api/simulate", {"scenario": "normal", "merchant_id": "zzz"})[0] == 400
    assert call(aws, "POST", "/api/alerts/not-an-id", {"action": "confirm"})[0] == 400
    assert call(aws, "POST", "/api/alerts/1789880000000-abcdef", {"action": "hack"})[0] == 400
