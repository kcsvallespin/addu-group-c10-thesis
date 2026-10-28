"""
batch_runner.py - generate the 30 benchmark scenarios and run them through run_scenario.py's pipeline.

Usage (PowerShell, from the folder that contains run_scenario.py and davao_venues.json paths):
    python batch_runner.py --make           # write scenarios.json (seed 42). Do this ONCE, then freeze the file.
    python batch_runner.py --limit 3        # try the first 3 scenarios (cheap smoke test)
    python batch_runner.py                  # run all scenarios; resumable (skips ids already in results.json)
    python batch_runner.py --summary        # print aggregate numbers from results.json
    python batch_runner.py --fresh          # ignore old results and start over

Outputs: scenarios.json, results.json (full detail), results.csv (one row per scenario),
         itineraries/<id>.json (final itinerary for each scenario).

Weather is FIXED per scenario (clear / mixed / rain), not fetched live, so every run is reproducible.
Attempt 1 is the "plain LLM" result (Phase 1 retrieval + prompt, no repair); later attempts are the repair loop.
"""
import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import time

import run_scenario as r   # reuses the prompt, candidate retrieval, checker and cost model

SCENARIOS_FILE = "scenarios.json"
RESULTS_FILE = "results.json"
CSV_FILE = "results.csv"
OUT_DIR = "itineraries"


def set_tag(tag):
    """--tag run2  ->  results_run2.json / results_run2.csv / itineraries_run2/ (keeps earlier runs intact)."""
    global RESULTS_FILE, CSV_FILE, OUT_DIR
    if tag:
        RESULTS_FILE, CSV_FILE, OUT_DIR = f"results_{tag}.json", f"results_{tag}.csv", f"itineraries_{tag}"
SEED = 42
N = 30
START_BASE = dt.date(2026, 10, 12)          # a Monday; start dates rotate through the week

BUDGETS_PER_PERSON_PER_DAY = [1000, 2000, 4000]   # daily ceiling = this x group_size
INTEREST_POOL = ["Food", "Nature", "Culture", "Entertainment", "Shopping", "Wellness", "Sightseeing"]


# ---------------------------------------------------------------- scenarios
def make_scenarios():
    rng = random.Random(SEED)
    durations = [1, 2, 3] * 10
    weathers = ["clear", "mixed", "rain"] * 10
    budgets = [BUDGETS_PER_PERSON_PER_DAY[i % 3] for i in range(N)]
    groups = [1, 2, 2, 3, 4, 5, 6, 2, 3, 4]
    for lst in (durations, weathers, budgets):
        rng.shuffle(lst)
    scenarios = []
    for i in range(N):
        n_int = rng.choice([1, 2, 2, 3, 3])
        interests = rng.sample(INTEREST_POOL, n_int)
        weather = weathers[i]
        if weather == "mixed" and durations[i] == 1:
            weather = "clear"                      # "mixed" needs at least 2 days
        group = rng.choice(groups)
        scenarios.append({
            "id": f"S{i + 1:02d}",
            "duration_days": durations[i],
            "budget_per_person_php": budgets[i],
            "budget_php": budgets[i] * group,      # per-day ceiling for the whole group
            "interests": interests,
            "group_size": group,
            "weather_pattern": weather,
            "start_date": str(START_BASE + dt.timedelta(days=i % 7)),
        })
    # a few scenarios that include hotels (multi-day, bigger budget) - accommodation is per room per night
    hotel_ids = [s for s in scenarios if s["duration_days"] >= 2 and s["budget_per_person_php"] == 4000][:3]
    for s in hotel_ids:
        if "Accommodation" not in s["interests"]:
            s["interests"] = s["interests"][:2] + ["Accommodation"]
    return scenarios


def weather_for(s):
    days = []
    start = dt.date.fromisoformat(s["start_date"])
    for d in range(s["duration_days"]):
        if s["weather_pattern"] == "clear":
            rainy = False
        elif s["weather_pattern"] == "rain":
            rainy = True
        else:
            rainy = (d % 2 == 1)                   # clear, rain, clear, ...
        days.append({"date": str(start + dt.timedelta(days=d)),
                     "condition": "Rain" if rainy else "Clear",
                     "pop": 1.0 if rainy else 0.1,
                     "is_outdoor_suitable": not rainy})
    return days


# ---------------------------------------------------------------- running
def classify(problem):
    p = problem.lower()
    rules = [("exceeds budget", "budget"), ("outside hours", "hours"), ("repeated", "duplicate"),
             ("interests not covered", "interest_coverage"), ("hallucinated", "hallucinated_venue"),
             ("slots (need", "slot_count"), ("weather conflict", "weather"), ("overlap", "overlap"),
             ("chronological", "order"), ("visit length", "duration"), ("outside 06:00", "time_window"),
             ("invalid time", "time_format"), ("not a trip date", "date"), ("appears more than once", "date"),
             ("days (need", "day_count"), ("json", "invalid_json")]
    for key, label in rules:
        if key in p:
            return label
    return "other"


def build_prompt(s, candidates, weather):
    context = {"user_constraints": {k: s[k] for k in ("duration_days", "budget_php", "interests", "group_size")},
               "weather_summary": {"forecast_by_date": weather},
               "candidate_venues": candidates}
    return (r.SYSTEM_PROMPT
            .replace("{duration_days}", str(s["duration_days"]))
            .replace("{budget_php}", str(s["budget_php"]))
            .replace("{interests}", ", ".join(s["interests"]))
            .replace("{group_size}", str(s["group_size"]))
            .replace("{context_payload_json}", json.dumps(context, ensure_ascii=False)))


def day_costs(itinerary, candidates, group_size):
    by_id = {c["venue_id"]: c for c in candidates}
    out = []
    for day in itinerary.get("itinerary", []):
        out.append(round(sum(r.venue_cost(by_id[sl["venue_id"]], group_size)
                             for sl in day.get("slots", []) if sl.get("venue_id") in by_id)))
    return out


def run_one(s):
    # point run_scenario's globals at this scenario
    r.SCENARIO.clear()
    r.SCENARIO.update({k: s[k] for k in ("duration_days", "budget_php", "interests", "group_size")})
    weather = weather_for(s)
    r.WEATHER = weather
    candidates = r.load_candidates()
    messages = [{"role": "user", "content": build_prompt(s, candidates, weather)}]

    costs = sorted(r.venue_cost(c, s["group_size"]) for c in candidates)
    min_day = sum(costs[:3])                       # cheapest possible 3-slot day from the candidate pool
    rec = {"id": s["id"], "scenario": s, "n_candidates": len(candidates), "attempts": [],
           "model": r.MODEL, "temperature": r.TEMPERATURE,
           "min_day_cost_php": round(min_day), "budget_feasible": min_day <= s["budget_php"],
           "prompt_tokens": 0, "completion_tokens": 0, "error": None}
    itinerary, t0 = None, time.time()
    for attempt in range(1, r.MAX_ATTEMPTS + 1):
        a0 = time.time()
        try:
            data = r.call_model(messages)
        except SystemExit:
            rec["error"] = "API error (see message above)"
            break
        usage = data.get("usage", {})
        rec["prompt_tokens"] += usage.get("prompt_tokens") or 0
        rec["completion_tokens"] += usage.get("completion_tokens") or 0
        raw, finish = r.get_content(data)
        try:
            itinerary = r.parse_json(raw)
            problems, warnings = r.check_output(itinerary, candidates)
        except json.JSONDecodeError as e:
            itinerary, warnings = None, []
            problems = [f"Response was not valid JSON ({e}; finish_reason={finish})"]
            r.ROUTE_STATS.update({"legs": 0, "tight_legs": 0, "km": 0.0})
        rec["attempts"].append({"attempt": attempt, "problems": problems, "warnings": warnings,
                                "problem_types": sorted({classify(p) for p in problems}),
                                "travel_gaps": r.ROUTE_STATS["tight_legs"],
                                "travel_legs": r.ROUTE_STATS["legs"],
                                "travel_km": round(r.ROUTE_STATS["km"], 1),
                                "finish_reason": finish,
                                "seconds": round(time.time() - a0, 1)})
        if not problems:
            break
        if raw:                                       # skip the assistant turn when the reply was empty
            messages.append({"role": "assistant", "content": raw})
        messages += [{"role": "user", "content": "Your itinerary violated these constraints:\n- "
                      + "\n- ".join(problems)
                      + "\nFix every problem and return the full corrected JSON object only. "
                        "Keep using only venue_ids from the candidate list."}]
    rec["total_seconds"] = round(time.time() - t0, 1)
    if rec["attempts"]:
        rec["first_attempt_pass"] = not rec["attempts"][0]["problems"]
        rec["final_pass"] = not rec["attempts"][-1]["problems"]
        rec["attempts_used"] = len(rec["attempts"])
    if itinerary:
        rec["day_costs_php"] = day_costs(itinerary, candidates, s["group_size"])
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f"{s['id']}.json"), "w", encoding="utf-8") as f:
            json.dump({"scenario": s, "weather": weather, "itinerary": itinerary}, f, indent=2, ensure_ascii=False)
    return rec


# ---------------------------------------------------------------- reporting
def rescore():
    """Re-check the saved FINAL itineraries of a previous run with the current checker (no API calls)."""
    if not os.path.isdir(OUT_DIR):
        raise SystemExit(f"No folder {OUT_DIR}/ - run with the matching --tag.")
    rows, types, total_legs, total_km = [], {}, [], []
    for fn in sorted(os.listdir(OUT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(OUT_DIR, fn), encoding="utf-8") as f:
            saved = json.load(f)
        s = saved["scenario"]
        r.SCENARIO.clear()
        r.SCENARIO.update({k: s[k] for k in ("duration_days", "budget_php", "interests", "group_size")})
        r.WEATHER = saved.get("weather") or weather_for(s)
        candidates = r.load_candidates()
        problems, warnings = r.check_output(saved["itinerary"], candidates)
        legs = r.ROUTE_STATS["tight_legs"]
        total_legs.append(r.ROUTE_STATS["legs"])
        total_km.append(r.ROUTE_STATS["km"])
        kinds = sorted({classify(p) for p in problems})
        for k in kinds:
            types[k] = types.get(k, 0) + 1
        rows.append((s["id"], kinds, legs))
        print(f"{s['id']}: {'PASS' if not problems else 'FAIL ' + str(kinds)}  "
              f"legs {r.ROUTE_STATS['legs']}, tight {legs}, {r.ROUTE_STATS['km']:.1f} km")
        for w in warnings:
            if w.startswith("travel:"):
                print("     ", w)
    n = len(rows)
    passed = sum(1 for _, k, _ in rows if not k)
    print(f"\nRe-scored {n} final itineraries with the stricter checker: {passed}/{n} pass.")
    print("Violations by type (itineraries affected):", dict(sorted(types.items(), key=lambda kv: -kv[1])) or "none")
    print(f"Travel: {sum(l for _, _, l in rows)} of {sum(total_legs)} legs are short by more than "
          f"{r.TRAVEL_TOLERANCE_MIN} min at {r.TRAVEL_SPEED_KMH} km/h; total {sum(total_km):.0f} km "
          f"(avg {sum(total_km) / max(sum(total_legs), 1):.1f} km per leg).")


def write_csv(results):
    cols = ["id", "duration_days", "budget_php", "group_size", "interests", "weather", "attempts_used",
            "first_attempt_pass", "final_pass", "budget_feasible", "first_attempt_problems", "final_problems", "travel_legs_final", "travel_tight_legs_final", "travel_km_final",
            "prompt_tokens", "completion_tokens", "total_seconds", "error"]
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for x in results:
            s = x["scenario"]
            at = x["attempts"]
            w.writerow([x["id"], s["duration_days"], s["budget_php"], s["group_size"],
                        "|".join(s["interests"]), s["weather_pattern"], x.get("attempts_used", ""),
                        x.get("first_attempt_pass", ""), x.get("final_pass", ""), x.get("budget_feasible", ""),
                        "|".join(at[0]["problem_types"]) if at else "",
                        "|".join(at[-1]["problem_types"]) if at else "",
                        at[-1].get("travel_legs", "") if at else "",
                        at[-1].get("travel_gaps", "") if at else "",
                        at[-1].get("travel_km", "") if at else "",
                        x["prompt_tokens"], x["completion_tokens"], x["total_seconds"], x["error"] or ""])


def summary(results):
    ok = [x for x in results if x["attempts"]]
    if not ok:
        print("No completed scenarios yet.")
        return
    n = len(ok)
    first = sum(1 for x in ok if x["first_attempt_pass"])
    final = sum(1 for x in ok if x["final_pass"])
    print(f"\nScenarios completed: {n}")
    print(f"Passed all checks on attempt 1 (no repair): {first}/{n} ({100 * first / n:.0f}%)")
    print(f"Passed after repair loop (max {r.MAX_ATTEMPTS} attempts): {final}/{n} ({100 * final / n:.0f}%)")
    types = {}
    for x in ok:
        for t in x["attempts"][0]["problem_types"]:
            types[t] = types.get(t, 0) + 1
    print("Attempt-1 violations by type (scenarios affected):", dict(sorted(types.items(), key=lambda kv: -kv[1])) or "none")
    left = {}
    for x in ok:
        for t in x["attempts"][-1]["problem_types"]:
            left[t] = left.get(t, 0) + 1
    print("Violations remaining after repair:", dict(left) or "none")
    legs = sum(x["attempts"][-1].get("travel_legs", 0) for x in ok)
    tight = sum(x["attempts"][-1].get("travel_gaps", 0) for x in ok)
    km = sum(x["attempts"][-1].get("travel_km", 0) for x in ok)
    print(f"Travel (soft metric): {tight} of {legs} legs in the final itineraries are short by more than "
          f"{r.TRAVEL_TOLERANCE_MIN} min at {r.TRAVEL_SPEED_KMH} km/h straight-line; total leg distance {km:.0f} km "
          f"(avg {km / max(legs, 1):.1f} km per leg).")
    infeasible = [x["id"] for x in ok if not x.get("budget_feasible", True)]
    if infeasible:
        print(f"Scenarios whose budget is impossible even with the 3 cheapest candidates: {infeasible} "
              "(consider excluding them from the pass-rate or loosening their budget)")
    secs = sorted(x["total_seconds"] for x in ok)
    print(f"Wall-clock per scenario (s): median {secs[len(secs) // 2]}, max {secs[-1]}")
    pt = sum(x["prompt_tokens"] for x in ok)
    ct = sum(x["completion_tokens"] for x in ok)
    cost = pt / 1e6 * 0.3045 + ct / 1e6 * 0.609
    print(f"Tokens: {pt:,} prompt + {ct:,} completion  (~${cost:.2f} at MiMo-V2.5-Pro list prices)")
    print("\nBy weather pattern (attempt-1 pass / final pass):")
    for wp in ("clear", "mixed", "rain"):
        g = [x for x in ok if x["scenario"]["weather_pattern"] == wp]
        if g:
            print(f"  {wp:6s} n={len(g):2d}  {sum(x['first_attempt_pass'] for x in g)} / {sum(x['final_pass'] for x in g)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--make", action="store_true", help="write scenarios.json and exit")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--tag", default=None, help="name for this run, e.g. run2 (separate results files)")
    ap.add_argument("--rescore", action="store_true", help="re-check saved final itineraries, no API calls")
    args = ap.parse_args()
    set_tag(args.tag)
    if args.rescore:
        rescore()
        return

    if args.make:
        if os.path.exists(SCENARIOS_FILE):
            print(f"{SCENARIOS_FILE} already exists - delete it first if you really want to regenerate "
                  "(it should stay frozen once you cite it in the paper).")
            return
        sc = make_scenarios()
        with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(sc, f, indent=2)
        print(f"Wrote {len(sc)} scenarios to {SCENARIOS_FILE}")
        for s in sc:
            print(f"  {s['id']}: {s['duration_days']}d  PHP{s['budget_php']}/day (group total)  group {s['group_size']}  "
                  f"{s['weather_pattern']:5s}  {', '.join(s['interests'])}")
        return

    results = []
    if os.path.exists(RESULTS_FILE) and not args.fresh:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            results = json.load(f)
    if args.summary:
        summary(results)
        return

    if not os.path.exists(SCENARIOS_FILE):
        raise SystemExit("Run `python batch_runner.py --make` first.")
    with open(SCENARIOS_FILE, encoding="utf-8") as f:
        scenarios = json.load(f)
    done = {x["id"] for x in results if not x["error"]}
    results = [x for x in results if x["id"] in done]
    todo = [s for s in scenarios if s["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done)} already done, running {len(todo)} scenario(s).")

    for s in todo:
        print(f"\n=== {s['id']}  {s['duration_days']}d  PHP{s['budget_php']}/day  group {s['group_size']}  "
              f"{s['weather_pattern']}  {s['interests']}")
        rec = run_one(s)
        results.append(rec)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:     # save after every scenario
            json.dump(results, f, indent=2, ensure_ascii=False)
        write_csv(results)
        if rec["error"]:
            print("Stopped because of an API error. Fix it and re-run; finished scenarios are kept.")
            break
        a = rec["attempts"]
        print(f"   attempt 1: {'PASS' if rec['first_attempt_pass'] else 'FAIL ' + str(a[0]['problem_types'])}"
              f" | final: {'PASS' if rec['final_pass'] else 'FAIL ' + str(a[-1]['problem_types'])}"
              f" | attempts {rec['attempts_used']} | {rec['total_seconds']}s | day costs {rec.get('day_costs_php')}")
    summary(results)


if __name__ == "__main__":
    main()