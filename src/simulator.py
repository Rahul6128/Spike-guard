"""Synthetic merchant traffic.

The demo runs on generated payments so the dashboard can show attacks on demand.
Real traffic goes through POST /api/ingest instead.
"""
from __future__ import annotations

import math
import random

MERCHANTS = [
    {"id": "m-jaipur-handloom", "name": "Pink City Handlooms", "home": "IN-Jaipur", "rate": 26, "avg": 1400},
    {"id": "m-bengaluru-brew", "name": "Namma Brew Co.", "home": "IN-Bengaluru", "rate": 48, "avg": 320},
    {"id": "m-delhi-electronics", "name": "Nehru Place Electronics", "home": "IN-Delhi", "rate": 22, "avg": 5200},
    {"id": "m-kolkata-sweets", "name": "Mishti Mahal Sweets", "home": "IN-Kolkata", "rate": 35, "avg": 450},
    {"id": "m-chennai-fresh", "name": "Marina Fresh Mart", "home": "IN-Chennai", "rate": 40, "avg": 780},
    {"id": "m-hyderabad-gadgets", "name": "Charminar Gadget Bazaar", "home": "IN-Hyderabad", "rate": 30, "avg": 2600},
]

SCENARIOS = ["normal", "flash_sale", "card_testing", "velocity", "geo", "bust_out"]

_ZONES = ["IN-Mumbai", "IN-Pune", "IN-Delhi", "IN-Bengaluru", "IN-Chennai", "IN-Kolkata",
          "IN-Hyderabad", "IN-Jaipur", "IN-Ahmedabad", "IN-Lucknow"]


def by_id(merchant_id):
    for m in MERCHANTS:
        if m["id"] == merchant_id:
            return m
    return None


def pick(merchant_id=None, rng=None):
    rng = rng or random
    return by_id(merchant_id) or rng.choice(MERCHANTS)


def _pools(m):
    r = random.Random(m["id"])
    bins = [str(r.randint(400000, 559999)) for _ in range(60)]
    zones = r.sample([z for z in _ZONES if z != m["home"]], 3)
    devices = ["dev-{}-{:02d}".format(m["id"][-6:], k) for k in range(18)]
    return bins, zones, devices


def _new_bin(rng):
    return str(rng.randint(400000, 559999))


def gen_window(kind, m, rng, end_ts, strength=1.0):
    """Return a list of payments for one 5-minute window ending at end_ts.

    strength: 1.0 = an obvious attack (used in the demo); lower values make the
    attack milder and harder to separate from normal traffic (used in the evaluation).
    """
    s = max(0.05, min(1.0, strength))
    bins, zones, devices = _pools(m)
    home, rate, avg = m["home"], m["rate"], m["avg"]
    weights = [1.0 / (i + 1) ** 0.6 for i in range(len(bins))]

    def ts():
        return int(end_ts - rng.uniform(0, 299))

    def amount():
        return max(1.0, round(rng.lognormvariate(math.log(avg * 0.75), 0.6), 2))

    def normal_geo():
        return home if rng.random() < 0.88 else rng.choice(zones)

    def normal_txn(declined_p=0.04, device=None, card_bin=None):
        return {
            "ts": ts(), "amount": amount(),
            "status": "declined" if rng.random() < declined_p else "approved",
            "device_id": device or rng.choice(devices),
            "card_bin": card_bin or rng.choices(bins, weights)[0],
            "geo": normal_geo(),
        }

    n_normal = max(4, int(rng.gauss(rate, rate ** 0.5)))

    if kind == "normal":
        return [normal_txn() for _ in range(n_normal)]

    if kind == "flash_sale":
        n = int(rate * rng.uniform(2.2, 3.0))
        crowd = ["dev-{}-cust-{:03d}".format(m["id"][-6:], k) for k in range(140)]
        return [normal_txn(device=rng.choice(crowd), card_bin=_new_bin(rng)) for _ in range(n)]

    if kind == "card_testing":
        n = int(rate * (1 + (rng.uniform(3.5, 6.0) - 1) * s))
        bots = ["dev-bot-{:02d}".format(k) for k in range(rng.randint(1, 3) if s > 0.6 else rng.randint(3, 6))]
        out = []
        for _ in range(n):
            tiny = rng.random() < 0.10 + 0.75 * s
            out.append({
                "ts": ts(),
                "amount": round(rng.uniform(1, 10) if tiny else amount(), 2),
                "status": "declined" if rng.random() < 0.04 + 0.74 * s else "approved",
                "device_id": rng.choice(bots) if rng.random() < s else rng.choice(devices),
                "card_bin": _new_bin(rng) if rng.random() < s else rng.choices(bins, weights)[0],
                "geo": home,
            })
        return out

    if kind == "velocity":
        n = int(rate * (1 + (rng.uniform(5.0, 8.0) - 1) * s))
        bots = ["dev-bot-{:02d}".format(k) for k in range(rng.randint(2, 4) if s > 0.6 else rng.randint(5, 8))]
        pool = [_new_bin(rng) for _ in range(rng.randint(8, 15))]
        return [{
            "ts": ts(), "amount": amount(),
            "status": "declined" if rng.random() < 0.10 else "approved",
            "device_id": rng.choice(bots), "card_bin": rng.choice(pool), "geo": home,
        } for _ in range(n)]

    if kind == "geo":
        n = int(rate * rng.uniform(1.1, 1.5))
        overseas = ["OVERSEAS-{:02d}".format(k) for k in rng.sample(range(1, 30), rng.randint(6, 10))]
        out = []
        for _ in range(n):
            far = rng.random() < rng.uniform(0.55, 0.70) * s
            out.append({
                "ts": ts(), "amount": amount(),
                "status": "declined" if rng.random() < 0.06 else "approved",
                "device_id": "dev-x-{:03d}".format(rng.randint(0, 400)),
                "card_bin": _new_bin(rng), "geo": rng.choice(overseas) if far else normal_geo(),
            })
        return out

    if kind == "bust_out":
        n = int(rate * rng.uniform(1.0, 1.3))
        k = max(1, int(n * rng.uniform(0.14, 0.22) * (0.4 + 0.6 * s)))
        big_floor = max(avg * (7 + 18 * s), 12000.0 + 28000.0 * s)
        out = [normal_txn(declined_p=0.02) for _ in range(n - k)]
        for _ in range(k):
            out.append({
                "ts": ts(), "amount": round(rng.uniform(big_floor, big_floor * 2.2), 2),
                "status": "approved", "device_id": rng.choice(devices),
                "card_bin": rng.choices(bins, weights)[0], "geo": home,
            })
        return out

    raise ValueError("unknown scenario: {}".format(kind))
