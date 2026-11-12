"""Local predictor: scores an entry idea 0-100 for "reaches +1R before -1R". Runs on your PC, no AI.

    python desk/predict.py train        # learn weights from labelled ideas (trader/replay/ideas.csv)

v1 uses hand-set weights (a guess). `train` fits a logistic regression on ideas the
replay labelled from real bars, tests it walk-forward (older days train, newer days
test) and saves the weights only if they beat the baseline on the unseen days.
Shadow mode by default: predictor_min 0 means the score is logged but never blocks.
"""
import csv
import math
import os
import sys

import common as C

FEATURES = ["vol_x", "breakout_pct", "range_pct", "minutes", "nifty_pct", "bullish"]
V1 = {"bias": -0.2, "vol_x": 0.15, "breakout_pct": -0.4, "range_pct": -0.2, "minutes": -0.004,
      "nifty_pct": 0.5, "bullish": 0.3}


def features(r, now, bullish=False):
    """Inputs from a breakout row (scan.orb_for) at the time of the idea."""
    nifty = 0.0
    try:
        day = C.closed_bars(C.by_day(C.fetch_bars("^NSEI", "5d", "5m")).get(now.date(), []), now)
        if day:
            nifty = (day[-1]["c"] - day[0]["o"]) / day[0]["o"] * 100
    except C.DeskError:
        pass
    return {"vol_x": min(r.get("volume_x") or 0, 6), "breakout_pct": (r["trigger_close"] - r["or_high"]) / r["or_high"] * 100,
            "range_pct": (r["or_high"] - r["or_low"]) / r["or_low"] * 100,
            "minutes": (now.hour * 60 + now.minute) - (9 * 60 + 15), "nifty_pct": nifty, "bullish": 1 if bullish else 0}


def weights():
    w = C.read_json("predictor.json")
    return w["weights"] if w else V1


def score(f, w=None):
    w = w or weights()
    z = w["bias"] + sum(w[k] * f[k] for k in FEATURES)
    return round(100 / (1 + math.exp(-max(-30, min(30, z)))), 1)


def label(entry, stop, bars, flat):
    """1 if price reaches +1R before -1R (stop checked first in a bar), else 0."""
    R = entry - stop
    for b in bars:
        if b["t"].time() >= flat or b["l"] <= stop:
            return 0
        if b["h"] >= entry + R:
            return 1
    return 0


def fit(rows, steps=3000, lr=0.05, l2=0.01):
    w = {k: 0.0 for k in ["bias"] + FEATURES}
    scale = {k: (max(abs(float(r[k])) for r in rows) or 1) for k in FEATURES}
    for _ in range(steps):
        g = {k: 0.0 for k in w}
        for r in rows:
            p = 1 / (1 + math.exp(-(w["bias"] + sum(w[k] * float(r[k]) / scale[k] for k in FEATURES))))
            e = p - int(r["label"])
            g["bias"] += e
            for k in FEATURES:
                g[k] += e * float(r[k]) / scale[k]
        for k in w:
            w[k] -= lr * (g[k] / len(rows) + (l2 * w[k] if k != "bias" else 0))
    return {"bias": w["bias"], **{k: w[k] / scale[k] for k in FEATURES}}


def train():
    path = C.state_path(os.path.join("replay", "ideas.csv"))
    if not os.path.exists(path):
        raise C.DeskError("no labelled ideas yet; run python desk/replay.py first")
    rows = sorted(csv.DictReader(open(path, encoding="utf-8")), key=lambda r: r["date"])
    days = sorted({r["date"] for r in rows})
    if len(rows) < 100 or len(days) < 10:
        raise C.DeskError("only %d ideas over %d days; need 100+ over 10+ days to train honestly" % (len(rows), len(days)))
    cut = days[int(len(days) * 0.7)]
    tr, te = [r for r in rows if r["date"] < cut], [r for r in rows if r["date"] >= cut]
    w = fit(tr)
    base = sum(int(r["label"]) for r in te) / len(te)
    kept = [r for r in te if score({k: float(r[k]) for k in FEATURES}, w) >= 55]
    hit = sum(int(r["label"]) for r in kept) / len(kept) if kept else 0
    print("Trained on %d ideas (%s to %s), tested on %d unseen ideas from %s." % (len(tr), days[0], cut, len(te), cut))
    print("Unseen days: all ideas reach +1R first %.0f%% of the time; ideas scoring 55+: %.0f%% (%d ideas)."
          % (base * 100, hit * 100, len(kept)))
    if kept and hit > base + 0.05 and len(kept) >= 20:
        C.write_json("predictor.json", {"weights": w, "trained_to": cut, "test_hit": hit, "test_base": base})
        print("Better than baseline by 5+ points on unseen days: weights saved (still shadow until predictor_min > 0).")
    else:
        print("Not clearly better than baseline on unseen days: weights NOT saved. Keep collecting data.")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["train"]:
            train()
        else:
            print(__doc__)
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
