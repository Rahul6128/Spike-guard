import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import engine  # noqa: E402
import simulator  # noqa: E402

M = simulator.MERCHANTS[0]


def _warm_state(rng, k=12):
    state = {"history": [], "footprint": {}}
    now = int(time.time())
    for _ in range(k):
        txns = simulator.gen_window("normal", M, rng, now)
        base, _ = engine.baseline_from(state["history"])
        feats = engine.compute_features(txns, state["footprint"], base["avg_amount"][0])
        state = {"history": engine.push(state["history"], feats),
                 "footprint": engine.grow_footprint(state["footprint"], txns)}
    return state


def _score(state, kind, rng, strength=1.0):
    txns = simulator.gen_window(kind, M, rng, int(time.time()), strength)
    base, conf = engine.baseline_from(state["history"])
    feats = engine.compute_features(txns, state["footprint"], base["avg_amount"][0])
    return engine.score_window(feats, base, conf)


def test_normal_traffic_stays_quiet():
    rng = random.Random(1)
    state = _warm_state(rng)
    flagged = sum(_score(state, "normal", rng)["flagged"] for _ in range(100))
    assert flagged == 0


def test_flash_sale_is_not_flagged():
    rng = random.Random(2)
    state = _warm_state(rng)
    results = [_score(state, "flash_sale", rng) for _ in range(60)]
    assert not any(r["flagged"] for r in results)
    assert "legitimate busy period" in results[0]["note"]


def test_each_attack_is_flagged_with_the_right_label():
    rng = random.Random(3)
    state = _warm_state(rng)
    expected = {"card_testing": "card_testing", "velocity": "velocity",
                "geo": "geo_anomaly", "bust_out": "bust_out"}
    for kind, pattern in expected.items():
        for _ in range(20):
            r = _score(state, kind, rng)
            assert r["flagged"], kind
            assert r["pattern"] == pattern, (kind, r["pattern"])
            assert r["reasons"] and r["action"]


def test_fraud_windows_are_not_learned():
    from pipeline import evaluate
    rng = random.Random(4)
    state = _warm_state(rng)
    before = len(state["history"])
    txns = simulator.gen_window("card_testing", M, rng, int(time.time()))
    result, new_state = evaluate(state, txns, engine.DEFAULT_THRESHOLD)
    assert result["flagged"]
    assert len(new_state["history"]) == before


def test_new_merchant_learns_instead_of_flagging():
    """A merchant much bigger than the generic priors must not be flagged forever."""
    from pipeline import evaluate
    rng = random.Random(5)
    big = simulator.MERCHANTS[2]  # Nehru Place Electronics, average ticket ~Rs 5,200
    state = {"history": [], "footprint": {}}
    for i in range(engine.MIN_HISTORY + 4):
        txns = simulator.gen_window("normal", big, rng, int(time.time()))
        result, state = evaluate(state, txns, engine.DEFAULT_THRESHOLD)
        assert not result["flagged"], "window %d flagged during/after learning" % i
    assert len(state["history"]) == engine.MIN_HISTORY + 4
    assert engine.baseline_from(state["history"])[1] != "low"


def test_bad_input_does_not_crash():
    feats = engine.compute_features([{"amount": "abc"}, {"amount": -5}, {}])
    assert feats["n"] == 3.0
    assert engine.compute_features([])["n"] == 0.0
