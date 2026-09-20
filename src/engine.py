"""SpikeGuard detection engine.

Standard library only, so it runs on AWS Lambda with no extra packaging.

Two layers, then an ensemble:

  1. Rolling baseline: every merchant gets its own "normal" (median / spread of 12
     features over its last 48 quiet 5-minute windows). A new window is turned
     into z-scores against that baseline.
  2. Fraud-pattern signatures: four patterns (card testing, velocity attack,
     geographic anomaly, bust-out) each turn the relevant z-scores into a 0-1
     score.
  3. Ensemble: the window score is the strongest pattern score. Windows that
     look busy but not suspicious (a legitimate flash sale) stay quiet because
     no signature fires.

A brand-new merchant is in a learning period for its first 6 windows: they are scored and
stored but never flagged, and they build that merchant's baseline.

The output is only ever a *flag for human review* with plain-English reasons.
Nothing here blocks a payment.
"""
from __future__ import annotations

import math
from statistics import median

SMALL_AMOUNT = 50.0          # INR. Card-testing probes are tiny.
BIG_MULTIPLE = 6.0           # "unusually large" = 6x the merchant's usual average ticket
DEFAULT_REF_AVG = 900.0
MIN_HISTORY = 6              # windows a merchant needs before its baseline is trusted
HISTORY_LIMIT = 48           # 48 x 5 min = the last 4 hours of quiet traffic
FOOTPRINT_LIMIT = 40
MIN_TXNS_TO_FLAG = 5
DEFAULT_THRESHOLD = 0.65

FEATURES = [
    "n", "fail_rate", "avg_amount", "max_amount", "small_ratio",
    "uniq_devices", "uniq_bins", "txn_per_device", "bins_per_device",
    "uniq_geo", "new_geo_share", "big_ratio",
]

# Generic (mean, std) for a small Indian merchant. Only used to score windows while a
# merchant is still in its learning period (it never raises alerts during that time).
PRIORS = {
    "n": (30.0, 8.0), "fail_rate": (0.04, 0.03), "avg_amount": (900.0, 350.0),
    "max_amount": (4500.0, 2000.0), "small_ratio": (0.02, 0.02),
    "uniq_devices": (12.0, 4.0), "uniq_bins": (18.0, 5.0),
    "txn_per_device": (2.5, 0.8), "bins_per_device": (1.6, 0.5),
    "uniq_geo": (3.0, 1.0), "new_geo_share": (0.01, 0.02), "big_ratio": (0.0, 0.01),
}

# Minimum spread per feature so a very steady merchant does not make tiny wobbles look huge.
_FLOOR = {
    "n": lambda m: max(2.0, 0.2 * m),
    "fail_rate": lambda m: 0.03,
    "avg_amount": lambda m: max(10.0, 0.15 * m),
    "max_amount": lambda m: max(50.0, 0.25 * m),
    "small_ratio": lambda m: 0.04,
    "uniq_devices": lambda m: max(1.0, 0.25 * m),
    "uniq_bins": lambda m: max(1.5, 0.2 * m),
    "txn_per_device": lambda m: max(0.5, 0.2 * m),
    "bins_per_device": lambda m: max(0.4, 0.2 * m),
    "uniq_geo": lambda m: 0.75,
    "new_geo_share": lambda m: 0.05,
    "big_ratio": lambda m: 0.03,
}

PATTERN_LABELS = {
    "card_testing": "Card testing",
    "velocity": "Velocity attack",
    "geo_anomaly": "Geographic anomaly",
    "bust_out": "Bust-out",
}

PATTERN_ACTIONS = {
    "card_testing": "Ask for stronger card verification (OTP / 3-D Secure) on this merchant and review the tiny declined attempts.",
    "velocity": "Rate-limit the busiest devices and review the burst before more payments settle.",
    "geo_anomaly": "Check the orders coming from regions this merchant has never sold to before shipping or settling them.",
    "bust_out": "Hold settlement on the large approved payments until someone has verified them.",
}

_APPROVED = ("approved", "success", "succeeded", "captured", "paid")


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v) or math.isinf(v) or v < 0:
        return 0.0
    return v


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def compute_features(txns, footprint=None, ref_avg=None) -> dict:
    """Turn one merchant's 5-minute window of transactions into features."""
    footprint = footprint or {}
    ref_avg = ref_avg if ref_avg and ref_avg > 0 else DEFAULT_REF_AVG
    n = len(txns)
    if n == 0:
        out = {f: 0.0 for f in FEATURES}
        out["approved_amount"] = 0.0
        return out

    amounts, approved_amounts = [], []
    devices, bins, geos = set(), set(), []
    declined = 0
    for t in txns:
        amount = _num(t.get("amount"))
        ok = str(t.get("status", "approved")).lower() in _APPROVED
        amounts.append(amount)
        if ok:
            approved_amounts.append(amount)
        else:
            declined += 1
        devices.add(str(t.get("device_id") or "unknown"))
        bins.add(str(t.get("card_bin") or "unknown"))
        geos.append(str(t.get("geo") or "unknown"))

    uniq_devices = len(devices)
    uniq_bins = len(bins)
    known = set(footprint)
    new_geo = sum(1 for g in geos if g not in known) if known else 0
    big = sum(1 for a in approved_amounts if a >= BIG_MULTIPLE * ref_avg)

    return {
        "n": float(n),
        "fail_rate": declined / n,
        "avg_amount": (sum(approved_amounts) / len(approved_amounts)) if approved_amounts else 0.0,
        "max_amount": max(approved_amounts) if approved_amounts else 0.0,
        "small_ratio": sum(1 for a in amounts if a <= SMALL_AMOUNT) / n,
        "uniq_devices": float(uniq_devices),
        "uniq_bins": float(uniq_bins),
        "txn_per_device": n / max(1, uniq_devices),
        "bins_per_device": uniq_bins / max(1, uniq_devices),
        "uniq_geo": float(len(set(geos))),
        "new_geo_share": new_geo / n,
        "big_ratio": big / n,
        "approved_amount": sum(approved_amounts),
    }


def baseline_from(history):
    """Per-feature (centre, spread) from this merchant's history, plus a confidence label.

    Uses the median and the median absolute deviation (MAD) instead of mean / standard
    deviation, so one or two contaminated windows (for example an attack that landed
    during the learning period) cannot drag the baseline.
    """
    if len(history) < MIN_HISTORY:
        return dict(PRIORS), "low"
    base = {}
    for f in FEATURES:
        vals = [float(h.get(f, 0.0)) for h in history]
        centre = median(vals)
        spread = 1.4826 * median([abs(v - centre) for v in vals])
        base[f] = (centre, spread)
    return base, ("high" if len(history) >= 12 else "medium")


def _zscores(feats, baseline):
    z = {}
    for f in FEATURES:
        m, s = baseline[f]
        z[f] = (feats[f] - m) / max(s, _FLOOR[f](m))
    return z


def _p(z, key):
    """Only upward deviations matter for spikes; cap so one huge z cannot dominate."""
    return max(0.0, min(z[key], 8.0))


def _pattern_scores(z):
    return {
        "card_testing": _sig(
            0.45 * _p(z, "fail_rate") + 0.35 * _p(z, "small_ratio")
            + 0.25 * _p(z, "uniq_bins") + 0.20 * _p(z, "bins_per_device") - 4.0),
        # Card testing is also a burst; subtract its tell-tales so the label stays specific.
        "velocity": _sig(
            0.35 * _p(z, "n") + 0.65 * _p(z, "txn_per_device")
            - 0.30 * (_p(z, "fail_rate") + _p(z, "small_ratio")) - 3.5),
        "geo_anomaly": _sig(
            0.60 * _p(z, "new_geo_share") + 0.40 * _p(z, "uniq_geo") - 3.5),
        "bust_out": _sig(
            0.50 * _p(z, "max_amount") + 0.40 * _p(z, "avg_amount")
            + 0.40 * _p(z, "big_ratio") - 6.0),
    }


def _rupees(x):
    return "\u20b9{:,.0f}".format(x)


def _reasons(pattern, f, base):
    m = {k: v[0] for k, v in base.items()}
    if pattern == "card_testing":
        return [
            "{:.0%} of payments were declined (usually {:.0%}).".format(f["fail_rate"], m["fail_rate"]),
            "{:.0%} of payments were under {} (usually {:.0%}).".format(f["small_ratio"], _rupees(SMALL_AMOUNT), m["small_ratio"]),
            "{:.0f} different card BINs from {:.0f} device(s) (usually {:.0f} from {:.0f}).".format(
                f["uniq_bins"], f["uniq_devices"], m["uniq_bins"], m["uniq_devices"]),
        ]
    if pattern == "velocity":
        return [
            "{:.0f} payments in 5 minutes (usually {:.0f}).".format(f["n"], m["n"]),
            "{:.0f} payments per device (usually {:.1f}), so a few devices are doing most of the work.".format(
                f["txn_per_device"], m["txn_per_device"]),
        ]
    if pattern == "geo_anomaly":
        return [
            "{:.0%} of payments came from regions this merchant has never sold to.".format(f["new_geo_share"]),
            "{:.0f} regions in a single window (usually {:.0f}).".format(f["uniq_geo"], m["uniq_geo"]),
        ]
    return [
        "Largest approved payment was {} (usually {}).".format(_rupees(f["max_amount"]), _rupees(m["max_amount"])),
        "{:.0%} of approved payments were unusually large (usually {:.0%}).".format(f["big_ratio"], m["big_ratio"]),
        "Average ticket {} against a usual {}.".format(_rupees(f["avg_amount"]), _rupees(m["avg_amount"])),
    ]


def score_window(feats, baseline, confidence="high", threshold=DEFAULT_THRESHOLD):
    z = _zscores(feats, baseline)
    scores = _pattern_scores(z)
    pattern = max(scores, key=scores.get)
    score = scores[pattern]
    learning = confidence == "low"
    # Learning period: with no history of its own, a merchant's normal cannot be told apart
    # from an attack, so these windows are learned from and never flagged.
    flagged = (not learning) and score >= threshold and feats["n"] >= MIN_TXNS_TO_FLAG
    severity = "none"
    if flagged:
        severity = "high" if score >= 0.90 else ("medium" if score >= 0.75 else "low")

    note = ""
    reasons = []
    if flagged:
        reasons = _reasons(pattern, feats, baseline)
    elif learning:
        note = ("Learning this merchant's normal traffic (needs {} windows before it can raise "
                "alerts).").format(MIN_HISTORY)
    elif z["n"] >= 2.5:
        usual = max(baseline["n"][0], 1.0)
        note = ("Volume is {:.1f}x usual, but decline rate, device mix and regions look normal, "
                "so it is treated as a legitimate busy period.").format(feats["n"] / usual)
    else:
        note = "Within this merchant's normal range."

    return {
        # A learning-period score is measured against generic priors, so it is not meaningful.
        "score": 0.0 if learning else round(score, 3),
        "flagged": bool(flagged),
        "severity": severity,
        "pattern": pattern if flagged else None,
        "pattern_label": PATTERN_LABELS[pattern] if flagged else None,
        "top_pattern": pattern,
        "pattern_scores": {k: round(v, 3) for k, v in scores.items()},
        "reasons": reasons,
        "action": PATTERN_ACTIONS[pattern] if flagged else None,
        "note": note,
        "confidence": confidence,
        "threshold": round(threshold, 3),
        "features": {k: round(float(v), 4) for k, v in feats.items()},
    }


def push(history, feats):
    """Append a quiet window to the baseline history (fraud windows are never learned)."""
    row = {k: round(float(feats[k]), 4) for k in FEATURES}
    return (list(history) + [row])[-HISTORY_LIMIT:]


def grow_footprint(footprint, txns):
    fp = dict(footprint or {})
    for t in txns:
        g = str(t.get("geo") or "unknown")
        fp[g] = fp.get(g, 0) + 1
    if len(fp) > FOOTPRINT_LIMIT:
        fp = dict(sorted(fp.items(), key=lambda kv: kv[1], reverse=True)[:FOOTPRINT_LIMIT])
    return fp
