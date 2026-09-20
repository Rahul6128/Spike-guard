# SpikeGuard: submission writeup (draft, edit before you submit)

Replace everything in [square brackets] with your own words and links.

## The problem

Small merchants lose money to fraud they only discover days later as chargebacks. Four
patterns cause most of the early damage: card testing (tiny declined payments from bots),
velocity attacks (a burst from a few devices), stolen-card orders from regions the merchant
has never sold to, and bust-out (a few very large approved payments). A cafe and an
electronics shop have completely different "normal", so one fixed rule fits neither.
[Add one sentence about why this matters to you, or a merchant you know.]

## What I built

SpikeGuard watches each merchant's payments in 5-minute windows, learns that merchant's own
normal, and flags a window for human review when it stops looking normal. Every alert says
why in plain English ("78% of payments declined, usually 4%") and suggests a next step. It
never blocks a payment. A reviewer confirms or dismisses each alert, and those decisions
drive a live precision number on the dashboard. A legitimate flash sale (2-3x volume from real
customers) is deliberately treated as normal.

Live demo: [YOUR DASHBOARD URL]
Code: [YOUR GITHUB REPO URL]

## Where AWS fits

* **API Gateway (HTTP API)** serves the dashboard and the JSON API, with throttling.
* **Lambda** runs the detection engine and the API (Python 3.12, no dependencies).
* **DynamoDB** stores merchant baselines, every scored window, alerts and counters (single table, on demand).
* **SNS** emails high and medium severity alerts to the reviewer.
* **CloudWatch** receives custom metrics (`SpikeGuard/AlertsRaised`, `WindowScore`) through Embedded Metric Format.
* **CloudFormation / SAM** defines the whole stack, deployable with one script.

## How well it works (and how far to trust it)

On 1,800 **synthetic** windows: clear attacks precision 1.00, recall 1.00; subtle attacks
precision 1.00, recall 0.90; 0 of 345 legitimate flash sales flagged. The generator and the
detector are mine, so this shows the detector behaves as designed, not that it beats real
fraud. Weakest spot: geographic anomaly on subtle attacks (recall 0.71).

Limitations I chose to state up front: synthetic data; a new merchant has a 6-window learning
period during which attacks are missed; only four known patterns.

## What I learned

[Write this yourself. Judges score it, and it has to be true. Prompts:
 - What was the first AWS service you had never used before this weekend, and what surprised you?
 - What broke while you deployed, and how did you fix it?
 - One design decision you changed your mind on. (Real example from this build: the detector
   first flagged a merchant with big-ticket sales forever because it never learned that merchant's
   normal. The fix was a learning period plus a median/MAD baseline.)]

## Blog post (optional, earns a chance at the blog prizes)

[Publish a version of this on AWS Builder Center and link it in your submission.]
