"""Glue between the engine and AWS: score a window, persist it, raise alerts."""
from __future__ import annotations

import os
import random
import time

import engine
import notify
import simulator
import store

WARMUP_WINDOWS = 10
MIN_BASELINE = 8

DEMO_PLAN = {
    "m-jaipur-handloom": {11: "card_testing"},
    "m-bengaluru-brew": {9: "velocity"},
    "m-delhi-electronics": {12: "bust_out"},
    "m-kolkata-sweets": {10: "flash_sale"},
    "m-chennai-fresh": {13: "geo"},
}


def threshold():
    try:
        return float(os.environ.get("FLAG_THRESHOLD", engine.DEFAULT_THRESHOLD))
    except ValueError:
        return engine.DEFAULT_THRESHOLD


def evaluate(state, txns, thr):
    """Score one window against a merchant's state. Fraud windows are never learned."""
    history = state.get("history", [])
    footprint = state.get("footprint", {})
    baseline, confidence = engine.baseline_from(history)
    feats = engine.compute_features(txns, footprint, baseline["avg_amount"][0])
    result = engine.score_window(feats, baseline, confidence, thr)
    if result["flagged"]:
        new_state = {"history": history, "footprint": footprint}
    else:
        new_state = {"history": engine.push(history, feats),
                     "footprint": engine.grow_footprint(footprint, txns)}
    return result, new_state


def warm_up(state, merchant, rng, thr, now):
    """Teach a brand-new demo merchant what 'normal' looks like from quiet windows."""
    for i in range(WARMUP_WINDOWS):
        txns = simulator.gen_window("normal", merchant, rng, now - (WARMUP_WINDOWS - i) * 300)
        _, state = evaluate(state, txns, thr)
    return state


def _verdict(merchant_id, merchant_name, result, alert):
    return {
        "merchant_id": merchant_id, "merchant_name": merchant_name,
        "score": result["score"], "flagged": result["flagged"], "severity": result["severity"],
        "pattern": result["pattern"], "pattern_label": result["pattern_label"],
        "top_pattern": result["top_pattern"], "pattern_scores": result["pattern_scores"],
        "reasons": result["reasons"], "action": result["action"], "note": result["note"],
        "confidence": result["confidence"], "features": result["features"],
        "alert_id": alert["sk"] if alert else None,
    }


def process(state, merchant_id, merchant_name, txns, end_ts, notify_reviewer=True):
    """Score a window and write everything it produces to AWS."""
    result, new_state = evaluate(state, txns, threshold())
    store.put_window(store.window_item(merchant_id, merchant_name, end_ts, result))
    store.put_merchant(merchant_id, new_state, merchant_name)

    deltas = {"windows_scored": 1}
    alert = None
    if result["flagged"]:
        alert = store.alert_item(merchant_id, merchant_name, end_ts, result)
        store.put_alert(alert)
        deltas["alerts_raised"] = 1
        deltas["exposure"] = alert["exposure"]
        if notify_reviewer:
            notify.publish_alert(alert)
    store.incr_stats(**deltas)
    notify.emit_metrics(result, merchant_id)
    return _verdict(merchant_id, merchant_name, result, alert)


def ingest(merchant_id, merchant_name, txns, end_ts):
    """Real traffic: POST /api/ingest."""
    return process(store.get_merchant(merchant_id), merchant_id, merchant_name, txns, end_ts)


def simulate(scenario, merchant_id=None):
    """Generate one window of synthetic traffic and run it through the real pipeline."""
    rng = random.Random()
    now = int(time.time())
    merchant = simulator.pick(merchant_id, rng)
    state = store.get_merchant(merchant["id"])
    warmed = False
    if len(state["history"]) < MIN_BASELINE:
        state = warm_up(state, merchant, rng, threshold(), now)
        warmed = True
    txns = simulator.gen_window(scenario, merchant, rng, now)
    verdict = process(state, merchant["id"], merchant["name"], txns, now)
    verdict["warmed_up"] = warmed
    verdict["scenario"] = scenario
    return verdict


def seed_demo():
    """Fill the dashboard with an hour of synthetic traffic for six merchants."""
    stats = store.get_stats()
    if stats["windows_scored"] > 0:
        return {"seeded": False, "message": "Demo data is already loaded."}

    rng = random.Random(7)
    now = int(time.time())
    thr = threshold()
    windows, alerts = [], []
    total_exposure = 0.0
    scored = 0
    windows_per_merchant = 14

    for merchant in simulator.MERCHANTS:
        state = {"history": [], "footprint": {}}
        plan = DEMO_PLAN.get(merchant["id"], {})
        for j in range(windows_per_merchant):
            end_ts = now - (windows_per_merchant - j) * 300
            kind = plan.get(j, "normal")
            txns = simulator.gen_window(kind, merchant, rng, end_ts)
            result, state = evaluate(state, txns, thr)
            windows.append(store.window_item(merchant["id"], merchant["name"], end_ts, result))
            scored += 1
            if result["flagged"]:
                alert = store.alert_item(merchant["id"], merchant["name"], end_ts, result)
                alerts.append(alert)
                total_exposure += alert["exposure"]
        store.put_merchant(merchant["id"], state, merchant["name"])

    store.batch_put(windows + alerts)
    store.incr_stats(windows_scored=scored, alerts_raised=len(alerts), exposure=total_exposure)
    return {"seeded": True, "windows": scored, "alerts": len(alerts)}
