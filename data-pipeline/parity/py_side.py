"""Builds parity test cases and the Python reference outputs.
usage: python py_side.py <data-pipeline dir> <out dir>"""
import copy
import json
import os
import random
import sys

sys.path.insert(0, sys.argv[1])
import run_scenario as r  # noqa: E402
import batch_runner as b  # noqa: E402

out_dir = sys.argv[2]
rng = random.Random(7)
INTERESTS = ["Food", "Nature", "Culture", "Entertainment", "Shopping", "Wellness", "Accommodation", "Sightseeing"]

# ---- requests: the 30 frozen scenarios + random app-style requests
reqs = []
for s in json.load(open(os.path.join(sys.argv[1], "scenarios.json"))):
    reqs.append({k: s[k] for k in ("duration_days", "budget_php", "interests", "group_size", "weather_pattern", "start_date")})
for _ in range(400):
    g = rng.randint(1, 20)
    reqs.append({"duration_days": rng.randint(1, 7), "budget_php": rng.choice([1, 50, 300, 999, 1000, 2000, 4000, 12345]) * g,
                 "interests": rng.sample(INTERESTS, rng.randint(1, 8)), "group_size": g,
                 "weather_pattern": rng.choice(["clear", "mixed", "rain"]),
                 "start_date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"})


def weird(x):
    return rng.choice([x, x, x, None, 5, 1.5, True, "", " 9:00", "09:00:00", "+8:30", "8.5:00", [1], {"a": 1}, "abc"])


def make_itins(req, cands, weather):
    out = []
    ids = [c["venue_id"] for c in cands] + ["dvo_poi_9999", "made_up"]
    dates = [w["date"] for w in weather] + ["2026-02-30", "2025-01-01", "bad"]
    for k in range(8):
        days = []
        for d in range(max(1, req["duration_days"] + rng.choice([0, 0, 0, -1, 1]))):
            slots = []
            t = rng.randint(5 * 60, 12 * 60)
            for _ in range(rng.choice([2, 3, 3, 4, 5, 6])):
                ln = rng.choice([20, 30, 60, 90, 120, 240, 300])
                s = {"time_start": f"{t // 60:02d}:{t % 60:02d}", "time_end": f"{(t + ln) // 60:02d}:{(t + ln) % 60:02d}",
                     "venue_id": rng.choice(ids if rng.random() < 0.1 else ids[:-2] or ids)}
                t += ln + rng.choice([-30, 0, 5, 15, 30, 60])
                if k >= 6:
                    for key in list(s):
                        if rng.random() < 0.2:
                            s[key] = weird(s[key])
                        if rng.random() < 0.05:
                            del s[key]
                slots.append(s)
            day = {"day": d + 1, "date": dates[d] if d < len(weather) and rng.random() < 0.9 else rng.choice(dates),
                   "slots": slots}
            if k == 7:
                if rng.random() < 0.2:
                    day["day"] = rng.choice([1.0, "1", True, None, 2.5])
                if rng.random() < 0.1:
                    del day[rng.choice(["day", "date", "slots"])]
            days.append(day)
        out.append({"itinerary": days})
    out += [[], {"itinerary": {}}, {"itinerary": [1]}, {"foo": 1}, {"itinerary": [{"slots": None}]},
            {"itinerary": [{"slots": [1]}]}, {"itinerary": [{"date": 5}]}, "itinerary text"]
    return out


cases = []
for req in reqs:
    r.SCENARIO.clear()
    r.SCENARIO.update({k: req[k] for k in ("duration_days", "budget_php", "interests", "group_size")})
    weather = b.weather_for(req)
    r.WEATHER = weather
    cands = r.load_candidates()
    itins = make_itins(req, cands, weather)
    checks = []
    for it in itins:
        p, w = r.check_output(copy.deepcopy(it), cands)
        checks.append({"problems": p, "warnings": w, "route": [r.ROUTE_STATS["legs"], r.ROUTE_STATS["tight_legs"]],
                       "repair": r.repair_message(p) if p else ""})
    cases.append({"req": req, "itins": itins,
                  "expect": {"weather": weather, "candidates": cands, "prompt": r.build_prompt(req, cands, weather),
                             "feasibility": r.feasibility(cands, req, weather), "checks": checks}})

raws = ['{"itinerary": []}', '```json\n{"itinerary": []}\n```', '<think>hmm {x}</think>\n{"a": 1}',
        'Here you go: {"a": {"b": 2}} thanks', '', '   ', 'NaN', '{"a": NaN}', '[1, 2]', '"str"', '{bad}', '```\n[1]\n```',
        '{"a": 1} {"b": 2}', 'text only', '{"a": -0}', '<think>a</think><think>b</think>{"z": 0}']
parse = []
for t in raws:
    try:
        parse.append({"ok": True, "value": r.parse_json(t)})
    except ValueError:
        parse.append({"ok": False})

rand = random.Random(42)
seq = []
for n in [1, 2, 3, 5, 10, 33, 100, 1000]:
    x = list(range(n))
    rand.shuffle(x)
    seq.append(x)

json.dump({"cases": cases, "raws": raws, "parse": parse, "shuffles": seq}, open(os.path.join(out_dir, "cases.json"), "w", encoding="utf-8"),
          ensure_ascii=False, allow_nan=False)
print(len(cases), "cases,", sum(len(c["itins"]) for c in cases), "itineraries")
