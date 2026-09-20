# Run SpikeGuard locally (no AWS account needed)

This runs the REAL Lambda code (app.py, pipeline.py, engine.py) with
DynamoDB and SNS mocked in-memory via `moto`. This is the same trick
the test suite (tests/test_api.py) already uses.

## Setup (one time)

    pip install boto3 "moto[dynamodb,sns]" flask

If pip complains about "externally managed environment", add
--break-system-packages to the command above.

## Run it

    cd spikeguard/src
    python local_server.py

Then open http://127.0.0.1:8000 in your browser.

## Demo it

1. Click "Load one hour of demo traffic" (or POST to /api/seed)
2. Try the scenario buttons to trigger fraud patterns
3. Confirm/dismiss alerts and watch the precision stat update

This is exactly what you screen-record for the demo video. Say in the
video and in your writeup that this is running with AWS mocked
locally (moto) because your fresh AWS account was still in Amazon's
verification hold -- the code is unmodified and is the same code
template.yaml deploys to real Lambda + DynamoDB + SNS.
