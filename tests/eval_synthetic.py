"""Reproducible evaluation on SYNTHETIC traffic.

    python tests/eval_synthetic.py

Six merchants, 300 windows each: 80% normal, 10% legitimate flash sales, 10% attacks
(card testing, velocity, geo anomaly, bust-out). Two difficulty settings:

  clear  - obvious attacks (strength 0.85 to 1.0)
  subtle - milder attacks that sit closer to normal traffic (strength 0.15 to 0.6)

The generator and the detector were written by the same person, so these numbers show
the detector works as designed. They do NOT predict performance on real payments.
"""
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402
import simulator  # noqa: E402

ATTACKS = ["card_testing", "velocity", "geo", "bust_out"]
LABEL = {"geo": "geo_anomaly"}


def run(strength, seed=42, windows=300):
    rng = random.Random(seed)
    now = int(time.time())
    tp = fp = fn = tn = 0
    flash_total = flash_flagged = 0
    per = {a: {"windows": 0, "caught": 0, "right_label": 0} for a in ATTACKS}

    for merchant in simulator.MERCHANTS:
        state = {"history": [], "footprint": {}}
        for _ in range(12):  # quiet warm-up
            txns = simulator.gen_window("normal", merchant, rng, now)
            base, _ = engine.baseline_from(state["history"])
            feats = engine.compute_features(txns, state["footprint"], base["avg_amount"][0])
            state = {"history": engine.push(state["history"], feats),
                     "footprint": engine.grow_footprint(state["footprint"], txns)}
        for _ in range(windows):
            r = rng.random()
            kind = "normal" if r < 0.80 else ("flash_sale" if r < 0.90 else rng.choice(ATTACKS))
            txns = simulator.gen_window(kind, merchant, rng, now, rng.uniform(*strength))
            base, conf = engine.baseline_from(state["history"])
            feats = engine.compute_features(txns, state["footprint"], base["avg_amount"][0])
            res = engine.score_window(feats, base, conf)
            attack = kind in ATTACKS
            if attack:
                per[kind]["windows"] += 1
                if res["flagged"]:
                    per[kind]["caught"] += 1
                    if res["pattern"] == LABEL.get(kind, kind):
                        per[kind]["right_label"] += 1
            if kind == "flash_sale":
                flash_total += 1
                flash_flagged += int(res["flagged"])
            if attack and res["flagged"]:
                tp += 1
            elif attack:
                fn += 1
            elif res["flagged"]:
                fp += 1
            else:
                tn += 1
            if not res["flagged"]:  # fraud windows are never learned
                state = {"history": engine.push(state["history"], feats),
                         "footprint": engine.grow_footprint(state["footprint"], txns)}

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "strength_range": list(strength),
        "windows": tp + fp + fn + tn,
        "attack_windows": tp + fn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "false_positives": fp, "false_negatives": fn,
        "legit_flash_sales": flash_total, "flash_sales_flagged": flash_flagged,
        "per_attack": {k: {**v, "recall": round(v["caught"] / v["windows"], 3) if v["windows"] else None}
                       for k, v in per.items()},
    }


if __name__ == "__main__":
    out = {
        "note": "Synthetic data, generator and detector by the same author. Not a real-world accuracy claim.",
        "clear_attacks": run((0.85, 1.0)),
        "subtle_attacks": run((0.15, 0.60), seed=43),
    }
    os.makedirs(os.path.join(HERE, "..", "results"), exist_ok=True)
    with open(os.path.join(HERE, "..", "results", "metrics.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
