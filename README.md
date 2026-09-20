# SpikeGuard: fraud spike radar for small merchants

Small merchants lose money to card testing, bot bursts, stolen-card orders from far away and
bust-out fraud, and usually find out days later from a chargeback. SpikeGuard watches each
merchant's payments in 5-minute windows, learns what *that* merchant's normal looks like, and
flags a window for a human to review the moment it stops looking normal. It explains why in
plain English and suggests a next step. **It never blocks a payment.** A person confirms or
dismisses every alert, and those decisions feed a live precision number.

Built for the WeMakeDevs x AWS **First Commit** hackathon (Bharat Builds Tour, Ship It track).

## What it detects

| Pattern | What it looks like in a 5-minute window |
|---|---|
| Card testing | Many tiny payments, mostly declined, many different card BINs, very few devices |
| Velocity attack | A sudden burst of payments from a handful of devices |
| Geographic anomaly | A large share of payments from regions this merchant has never sold to |
| Bust-out | A few very large approved payments, far above the merchant's usual ticket |

A legitimate flash sale (2-3x volume from real customers) is deliberately treated as normal.

## How it works on AWS

```
Browser / your payment system
        |
   API Gateway (HTTP API, throttled)
        |
     Lambda (Python 3.12, no dependencies)  ---->  CloudWatch Logs + custom metrics (EMF)
        |             |
   DynamoDB          SNS  ---->  email alert to the on-call reviewer
 (baselines, windows,
  alerts, counters)
```

* **API Gateway** serves the dashboard and the JSON API. Rate limits protect the public demo.
* **Lambda** runs the detection engine (pure Python) and the API.
* **DynamoDB** (on-demand) stores per-merchant baselines, every scored window, alerts and counters.
* **SNS** emails high and medium severity alerts.
* **CloudWatch** receives `SpikeGuard/WindowsScored`, `AlertsRaised` and `WindowScore` metrics,
  written as Embedded Metric Format log lines (no extra API calls, no extra cost).
* Everything is one CloudFormation/SAM template: `template.yaml`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api/summary` | Everything the dashboard shows |
| POST | `/api/ingest` | Score a real 5-minute window: `{"merchant_id","merchant_name","transactions":[{"amount","status","device_id","card_bin","geo"}]}` |
| POST | `/api/simulate` | Score one synthetic scenario (demo) |
| POST | `/api/seed` | Load one hour of synthetic traffic (only when empty) |
| POST | `/api/alerts/{id}` | Reviewer decision: `{"action":"confirm"}` or `{"action":"dismiss"}` |

Set the `IngestApiKey` parameter to require an `x-api-key` header on `/api/ingest`.

## Tests and evaluation

```bash
pip install -r requirements-dev.txt
python -m pytest -q                 # 14 tests: engine + the real Lambda handler on mocked DynamoDB/SNS
python tests/eval_synthetic.py      # writes results/metrics.json
cfn-lint template.yaml
```

Results on **synthetic** traffic (1,800 windows, 6 merchants, 80% normal, 10% legitimate flash
sales, 10% attacks):

| Attack difficulty | Precision | Recall | Legit flash sales flagged |
|---|---|---|---|
| Clear attacks | 1.00 | 1.00 | 0 of 169 |
| Subtle attacks | 1.00 | 0.90 | 0 of 176 |

Per pattern, subtle attacks: card testing 0.88, velocity 1.00, geographic anomaly 0.71, bust-out 1.00.

**Read these numbers carefully.** The traffic generator and the detector were written by the
same person, so this shows the detector behaves as designed. It is **not** a claim about real
payment fraud.

## Honest limitations

* Demo traffic is synthetic. Real deployment needs recalibration on real merchant data and labels.
* A new merchant has a learning period: its first 6 windows are scored and stored but never
  flagged. An attack in that period is missed. The baseline uses the median and MAD so one or
  two bad windows during learning do not poison it.
* Geographic anomaly is the weakest pattern on subtle attacks (recall 0.71).
* Only four known patterns are recognised. A completely new attack style would not be labelled.
* The detector is rule-and-statistics based, not a trained model, so every alert can be explained.
* `INGEST_API_KEY` is passed to Lambda as an environment variable. Fine for a demo, use Secrets
  Manager for production.

## Repo layout

```
template.yaml        CloudFormation / SAM: API Gateway, Lambda, DynamoDB, SNS, log group
deploy.sh            one-command deploy (works in AWS CloudShell)
teardown.sh          delete everything
src/engine.py        features, per-merchant baseline, four pattern signatures, ensemble
src/pipeline.py      score -> store -> alert flow, demo seeding
src/store.py         DynamoDB access
src/notify.py        SNS alerts, CloudWatch metrics
src/simulator.py     synthetic merchants and attack scenarios
src/app.py           Lambda handler and routes
src/dashboard.html   the dashboard
tests/               pytest suite and the synthetic evaluation
docs/                architecture, submission writeup draft, demo video script
```
