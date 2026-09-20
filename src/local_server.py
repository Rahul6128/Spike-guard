"""
Run the REAL SpikeGuard Lambda handler locally, with DynamoDB and SNS
mocked in-memory via moto. No AWS account, no CloudShell, no deploy.

This is the exact same app.py / pipeline.py / engine.py that ships to
Lambda in template.yaml -- only the DynamoDB/SNS backend is swapped
for an in-process fake, using the same moto library the test suite
already depends on (see tests/test_api.py).

Usage:
    pip install boto3 "moto[dynamodb,sns]" flask
    python src/local_server.py
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
os.environ.setdefault("TABLE_NAME", "spikeguard-local")
os.environ.setdefault("FLAG_THRESHOLD", "0.65")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from moto import mock_aws  # noqa: E402

_mock = mock_aws()
_mock.start()

import boto3  # noqa: E402

ddb = boto3.client("dynamodb")
ddb.create_table(
    TableName=os.environ["TABLE_NAME"],
    BillingMode="PAY_PER_REQUEST",
    AttributeDefinitions=[
        {"AttributeName": "pk", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"},
    ],
    KeySchema=[
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ],
)
topic_arn = boto3.client("sns").create_topic(Name="spikeguard-local")["TopicArn"]
os.environ["TOPIC_ARN"] = topic_arn

import app  # noqa: E402  (the real Lambda handler)

from flask import Flask, request, Response  # noqa: E402

web = Flask(__name__)


@web.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@web.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy(path):
    event = {
        "requestContext": {"http": {"method": request.method}},
        "rawPath": "/" + path,
        "headers": {k: v for k, v in request.headers.items()},
        "queryStringParameters": dict(request.args) or None,
        "body": request.get_data(as_text=True) or None,
    }
    result = app.handler(event)
    return Response(
        result["body"],
        status=result["statusCode"],
        content_type=result["headers"].get("Content-Type", "application/json"),
    )


if __name__ == "__main__":
    print("=" * 60)
    print("SpikeGuard running locally (mocked AWS via moto)")
    print("This is the real Lambda code -- app.py, pipeline.py, engine.py")
    print("Open: http://127.0.0.1:8000")
    print("=" * 60)
    web.run(host="127.0.0.1", port=8000, debug=False)
