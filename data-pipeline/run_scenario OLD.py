import os
import json
import math
import random
import re
import time
import datetime as dt
import requests

# ---------------- Config ----------------
MODEL = os.environ.get("MODEL", "xiaomi/mimo-v2.5-pro")
TEMPERATURE = 0.2   # fixed for reproducibility; report this value in the paper
VENUES_FILE = os.path.join("..", "data-pipeline", "davao_venues.json")
SEED = 42
PER_INTEREST = 8          # candidates pulled per requested interest
MAX_CANDIDATES = 24
MAX_ATTEMPTS = 3          # initial try + 2 repair rounds       # keeps the payload small

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("Set OPENROUTER_API_KEY environment variable first")

# Test scenario (replace with one of your 30 later)
SCENARIO = {
    "duration_days": 2,
    "budget_php": 2500,
    "interests": ["Food", "Nature"],
    "group_size": 2,
}

# ---- Cost model (assumed values - see paper; Google gives categories, not amounts) ----
PER_PERSON = {"Free": 0, "Inexpensive": 150, "Moderate": 450,
              "Expensive": 1000, "Very Expensive": 2000, None: 450}
PER_ROOM_NIGHT = {"Inexpensive": 1000, "Moderate": 2500,
                  "Expensive": 5000, "Very Expensive": 9000, None: 2500}
COST_SCALE = 1.0   # set to 0.75 / 1.25 for the sensitivity check


def range_midpoint(text):
    """'PHP 500 - PHP 1000' -> 750.0 ; None if it can't be parsed."""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", text or "")]
    if len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    return nums[0] if nums else None


def venue_cost(v, group_size):
    """Estimated peso cost of one visit for the whole group."""
    cat = v.get("price_category")
    accommodation = "Accommodation" in (v.get("category") or [])
    if accommodation:
        base = PER_ROOM_NIGHT.get(cat, PER_ROOM_NIGHT[None])
        return COST_SCALE * base * math.ceil(group_size / 2)
    mid = range_midpoint(v.get("price_range_text"))
    per_person = mid if mid is not None else PER_PERSON.get(cat, PER_PERSON[None])
    return COST_SCALE * per_person * group_size


WEATHER = []

# ---- Checker settings ----
MIN_VISIT_MIN = 30
MAX_VISIT_MIN = 240            # not applied to Accommodation
DAY_START_MIN = 6 * 60
DAY_END_MIN = 23 * 60
TRAVEL_SPEED_KMH = 25          # urban Davao assumption; used for the soft travel check only
TRAVEL_TOLERANCE_MIN = 10      # a leg is "tight" only if the gap is more than this many minutes short
ROUTE_STATS = {"legs": 0, "tight_legs": 0, "km": 0.0}   # filled in by every check_output() call


def haversine_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SYSTEM_PROMPT = """You are the itinerary planning engine for a Davao City tourism
application. Generate a structured, multi-day travel itinerary using
ONLY the candidate venues provided in the context payload below. Do
not invent, assume, or reference any venue not present in the
candidate list.

CONSTRAINTS:
- Trip duration: {duration_days} day(s)
- Daily budget ceiling: PHP {budget_php}
- Interest categories: {interests}
- Group size: {group_size}

RULES:
1. Each day must contain 3-5 scheduled venue visits with realistic
   time slots (e.g. 08:00-10:00).
2. NEVER use the same venue_id more than once in the whole itinerary.
3. Do not schedule a venue outside its operating_hours for that
   weekday. If operating_hours is null, hours are unknown: avoid the
   venue unless nothing else fits.
4. Do not schedule a Nature venue during a day where the weather
   context marks is_outdoor_suitable as false.
5. Each venue has est_group_cost_php: the estimated cost of ONE visit
   for the whole group. The sum of est_group_cost_php for all venues
   scheduled on a day MUST NOT exceed the daily budget ceiling. Use
   it; do not invent other peso amounts.
6. Cover every requested interest category across the trip, and
   sequence venues in a geographically reasonable order (latitude/
   longitude are given; a downstream validator optimizes the route).
7. Within a day, list slots in chronological order with NO overlap.
   Each visit lasts between 30 minutes and 4 hours and must fall
   between 06:00 and 23:00.
8. Return exactly one itinerary entry per trip day, using exactly the
   dates in the weather forecast.

Respond with ONLY a single JSON object with this shape:
{"itinerary": [{"day": int, "date": "YYYY-MM-DD", "slots": [{"time_start": "HH:MM",
"time_end": "HH:MM", "venue_id": "<id from candidates>"}]}]}
Do not include any explanation, markdown formatting, or text outside the JSON object.

CONTEXT PAYLOAD:
{context_payload_json}
"""


# ---------------- Phase 1: context ----------------
def load_candidates():
    with open(VENUES_FILE, encoding="utf-8") as f:
        venues = json.load(f)
    rng = random.Random(SEED)
    chosen, seen_ids, seen_names = [], set(), set()

    for interest in SCENARIO["interests"]:
        pool = [v for v in venues
                if interest in (v.get("category") or [])
                and v.get("latitude") is not None
                and v.get("operating_hours")]            # prefer venues with known hours
        # quality score: rating weighted by popularity
        pool.sort(key=lambda v: (v.get("average_rating") or 0) * math.log10((v.get("rating_count") or 0) + 10),
                  reverse=True)
        top = pool[:PER_INTEREST * 4]                    # shortlist, then sample for variety
        rng.shuffle(top)
        cheap = sorted(top, key=lambda v: venue_cost(v, 1))[:PER_INTEREST // 2]   # guarantee affordable options
        top = cheap + [v for v in top if v not in cheap]
        taken = 0
        for v in top:
            name = (v["name"] or "").strip().lower()
            if v["venue_id"] in seen_ids or name in seen_names:   # drop chain-branch duplicates
                continue
            seen_ids.add(v["venue_id"])
            seen_names.add(name)
            chosen.append(v)
            taken += 1
            if taken >= PER_INTEREST:
                break

    out = []
    for v in chosen[:MAX_CANDIDATES]:
        out.append({
            "venue_id": v["venue_id"],
            "name": v["name"],
            "category": v["category"],
            "price_category": v.get("price_category") if v.get("price_category") != "Not Available" else None,
            "price_range_text": v.get("price_range_text"),
            "est_group_cost_php": round(venue_cost(v, SCENARIO["group_size"])),
            "average_rating": v.get("average_rating"),
            "operating_hours": v.get("operating_hours"),
            "latitude": v["latitude"],
            "longitude": v["longitude"],
        })
    return out


def get_weather():
    """5-day/3-hour forecast for Davao, collapsed to one entry per date."""
    n = SCENARIO["duration_days"]
    if not OPENWEATHERMAP_API_KEY:
        print("No OPENWEATHERMAP_API_KEY set - using a stub forecast.")
        start = dt.date.today() + dt.timedelta(days=1)
        return [{"date": str(start + dt.timedelta(days=i)), "condition": "Clear",
                 "pop": 0.1, "is_outdoor_suitable": True} for i in range(n)]
    resp = requests.get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={"q": "Davao City,PH", "appid": OPENWEATHERMAP_API_KEY, "units": "metric"},
        timeout=30,
    ).json()
    tz = resp.get("city", {}).get("timezone", 8 * 3600)      # seconds from UTC (Davao = +8h)
    today_local = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=tz)).date()
    days = {}
    for e in resp.get("list", []):
        local = dt.datetime.fromtimestamp(e["dt"], dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=tz)
        d = str(local.date())
        if local.date() <= today_local:                       # plan from tomorrow, in local time
            continue
        cur = days.setdefault(d, {"date": d, "condition": e["weather"][0]["main"], "pop": 0.0})
        cur["pop"] = max(cur["pop"], float(e.get("pop", 0)))
        if e["weather"][0]["main"] == "Rain":
            cur["condition"] = "Rain"
    result = list(days.values())[:n]
    for d in result:
        d["is_outdoor_suitable"] = d["pop"] < 0.5
    return result


# ---------------- Phase 2: model call ----------------
MAX_RETRIES = 4            # extra tries for dropped connections, timeouts, 429 and 5xx errors
RETRY_STATUS = {429, 500, 502, 503, 504}


def call_model(messages):
    """POST to OpenRouter. Retries network drops and temporary server errors with backoff;
    any other non-200 response (bad key, no credits, retired model) stops immediately."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            wait = 5 * 2 ** (attempt - 1)
            print(f"   retry {attempt}/{MAX_RETRIES} in {wait}s ({last_error})")
            time.sleep(wait)
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages, "temperature": TEMPERATURE},
                timeout=(10, 900),            # 10 s to connect, up to 15 min waiting for the answer
            )
        except requests.exceptions.RequestException as e:      # dropped connection, timeout, ...
            last_error = type(e).__name__
            continue
        print(f"Status code: {resp.status_code}")
        if resp.status_code in RETRY_STATUS:
            last_error = f"HTTP {resp.status_code}"
            continue
        if resp.status_code != 200:
            print(resp.text)
            raise SystemExit(1)
        try:
            return resp.json()
        except ValueError:
            last_error = "empty or invalid response body"
    print(f"Giving up after {MAX_RETRIES} retries: {last_error}")
    raise SystemExit(1)


def get_content(data):
    """(text, finish_reason). Empty text when the model returned no content (e.g. reasoning used up the budget)."""
    choices = data.get("choices") or []
    if not choices:
        return "", f"no choices ({str(data.get('error'))[:200]})"
    choice = choices[0]
    msg = choice.get("message") or {}
    return (msg.get("content") or ""), choice.get("finish_reason")


def parse_json(text):
    if not text or not text.strip():
        raise json.JSONDecodeError("empty response", "", 0)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ---------------- Checks ----------------
def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def within_hours(hours_dict, date_str, start, end):
    """True/False, or None when hours are unknown."""
    if not hours_dict:
        return None
    try:
        wd = DAYS[dt.date.fromisoformat(date_str).weekday()]
    except ValueError:
        return None
    rng = hours_dict.get(wd)
    if rng is None:
        return None
    if rng == "closed":
        return False
    s, e = to_min(start), to_min(end)
    for part in rng.split(","):
        o, c = (to_min(x) for x in part.strip().split("-"))
        if c <= o:           # overnight range
            c += 24 * 60
        if o <= s and e <= c:
            return True
    return False


def parse_time(t):
    """'HH:MM' -> minutes, or None if malformed."""
    try:
        h, m = str(t).split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 24 and 0 <= m < 60:
            return h * 60 + m
    except ValueError:
        pass
    return None


def check_output(data, candidates):
    """Returns (problems, warnings). Problems are hard violations that trigger repair.
    Warnings starting with 'travel:' are a soft metric (route quality is Phase 3's job)."""
    by_id = {c["venue_id"]: c for c in candidates}
    problems, warnings = [], []
    ROUTE_STATS.update({"legs": 0, "tight_legs": 0, "km": 0.0})
    if "itinerary" not in data:
        return ["Missing top-level 'itinerary' key"], []

    trip_dates = [w["date"] for w in WEATHER]
    rain_by_date = {w["date"]: not w.get("is_outdoor_suitable", True) for w in WEATHER}
    days_list = data["itinerary"]
    if len(days_list) != SCENARIO["duration_days"]:
        problems.append(f"Itinerary has {len(days_list)} days (need {SCENARIO['duration_days']})")

    used = []
    day_costs = {}
    seen_dates = []
    for day in days_list:
        for key in ("day", "date", "slots"):
            if key not in day:
                problems.append(f"Day missing '{key}'")
        date = day.get("date", "")
        if trip_dates and date not in trip_dates:
            problems.append(f"Day {day.get('day')}: date {date} is not a trip date {trip_dates}")
        elif date in seen_dates:
            problems.append(f"Date {date} appears more than once")
        seen_dates.append(date)

        slots = day.get("slots", [])
        if not 3 <= len(slots) <= 5:
            problems.append(f"Day {day.get('day')}: {len(slots)} slots (need 3-5)")

        timed = []                                   # (start, end, venue) for the order/overlap/travel checks
        for slot in slots:
            for key in ("time_start", "time_end", "venue_id"):
                if key not in slot:
                    problems.append(f"Slot missing '{key}'")
            vid = slot.get("venue_id")
            if vid not in by_id:
                problems.append(f"Hallucinated venue_id: {vid}")
                continue
            v = by_id[vid]
            used.append(vid)
            day_costs[day.get("day")] = day_costs.get(day.get("day"), 0) + venue_cost(v, SCENARIO["group_size"])

            s_min, e_min = parse_time(slot.get("time_start")), parse_time(slot.get("time_end"))
            if s_min is None or e_min is None or e_min <= s_min:
                problems.append(f"{vid} ({v['name']}): invalid time range "
                                f"{slot.get('time_start')}-{slot.get('time_end')}")
                continue
            timed.append((s_min, e_min, v))

            length = e_min - s_min
            if length < MIN_VISIT_MIN or (length > MAX_VISIT_MIN and "Accommodation" not in v["category"]):
                problems.append(f"{vid} ({v['name']}): visit length {length} min is outside "
                                f"{MIN_VISIT_MIN}-{MAX_VISIT_MIN} min")
            if s_min < DAY_START_MIN or e_min > DAY_END_MIN:
                problems.append(f"{vid} ({v['name']}): {slot['time_start']}-{slot['time_end']} is outside 06:00-23:00")

            if rain_by_date.get(date) and "Nature" in v["category"]:
                problems.append(f"Weather conflict: {vid} ({v['name']}) is outdoors on {date}, "
                                "which is marked unsuitable for outdoor visits")

            try:
                ok = within_hours(v["operating_hours"], date, slot["time_start"], slot["time_end"])
            except (KeyError, ValueError):
                ok = None
            if ok is False:
                problems.append(f"{vid} ({v['name']}) scheduled outside hours on {date}")
            elif ok is None:
                warnings.append(f"{vid} ({v['name']}) has unknown hours")

        # order + overlap (listed order must be chronological)
        for (s1, e1, v1), (s2, e2, v2) in zip(timed, timed[1:]):
            if s2 < s1:
                problems.append(f"Day {day.get('day')}: slots are not in chronological order "
                                f"({v1['venue_id']} then {v2['venue_id']})")
            elif s2 < e1:
                problems.append(f"Day {day.get('day')}: slots overlap ({v1['venue_id']} and {v2['venue_id']})")
            else:                                    # soft travel-time check (straight-line lower bound)
                km = haversine_km(v1, v2)
                need = km / TRAVEL_SPEED_KMH * 60
                gap = s2 - e1
                ROUTE_STATS["legs"] += 1
                ROUTE_STATS["km"] += km
                if need - gap > TRAVEL_TOLERANCE_MIN:
                    ROUTE_STATS["tight_legs"] += 1
                    warnings.append(f"travel: day {day.get('day')} {v1['venue_id']}->{v2['venue_id']} "
                                    f"{km:.1f} km needs ~{need:.0f} min, gap is {gap} min "
                                    f"(short by {need - gap:.0f})")

    for d, cost in sorted(day_costs.items(), key=lambda x: str(x[0])):
        if cost > SCENARIO["budget_php"]:
            problems.append(f"Day {d}: estimated cost PHP {cost:,.0f} exceeds budget PHP {SCENARIO['budget_php']:,}")
        else:
            warnings.append(f"Day {d}: estimated cost PHP {cost:,.0f} (within budget)")

    dupes = {v for v in used if used.count(v) > 1}
    if dupes:
        problems.append(f"Repeated venues: {sorted(dupes)}")

    covered = {i for v in used for i in by_id[v]["category"]}
    missing = [i for i in SCENARIO["interests"] if i not in covered]
    if missing:
        outdoor_days = [w for w in WEATHER if w.get("is_outdoor_suitable")]
        if "Nature" in missing and not outdoor_days:
            warnings.append("Nature not covered, but the forecast marks every day as unsuitable for outdoor visits")
            missing = [m for m in missing if m != "Nature"]
        if missing:
            problems.append(f"Interests not covered: {missing}")
    return problems, warnings


def main():
    candidates = load_candidates()
    print(f"{len(candidates)} candidates selected.")
    global WEATHER
    WEATHER = get_weather()
    print("Weather:", [(w["date"], w["condition"], round(w["pop"], 2), w["is_outdoor_suitable"]) for w in WEATHER])
    context = {
        "user_constraints": SCENARIO,
        "weather_summary": {"forecast_by_date": WEATHER},
        "candidate_venues": candidates,
    }
    prompt = (SYSTEM_PROMPT
              .replace("{duration_days}", str(SCENARIO["duration_days"]))
              .replace("{budget_php}", str(SCENARIO["budget_php"]))
              .replace("{interests}", ", ".join(SCENARIO["interests"]))
              .replace("{group_size}", str(SCENARIO["group_size"]))
              .replace("{context_payload_json}", json.dumps(context, ensure_ascii=False)))

    messages = [{"role": "user", "content": prompt}]
    itinerary, problems, warnings = None, [], []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n--- Attempt {attempt}/{MAX_ATTEMPTS} ---")
        data = call_model(messages)
        usage = data.get("usage", {})
        print(f"Tokens - prompt: {usage.get('prompt_tokens')}, completion: {usage.get('completion_tokens')}")
        raw, finish = get_content(data)
        try:
            itinerary = parse_json(raw)
        except json.JSONDecodeError as e:
            print(f"Response was not valid JSON: {e} (finish_reason={finish})")
            problems = [f"Response was not valid JSON ({e}). Reply with ONLY the JSON object."]
        else:
            problems, warnings = check_output(itinerary, candidates)
        print("Checks:", "PASSED" if not problems else "FAILED")
        for p in problems:
            print("  PROBLEM:", p)
        for w in warnings:
            print("  warning:", w)
        if not problems:
            break
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your itinerary violated these constraints:\n- "
             + "\n- ".join(problems)
             + "\nFix every problem and return the full corrected JSON object only. "
               "Keep using only venue_ids from the candidate list."},
        ]
    if itinerary is None:
        raise SystemExit(1)
    print(f"\nFinal result after {attempt} attempt(s): {'PASSED' if not problems else 'STILL FAILING'}")

    names = {c["venue_id"]: c["name"] for c in candidates}
    for day in itinerary.get("itinerary", []):
        print(f"\nDay {day.get('day')} ({day.get('date')})")
        for s in day.get("slots", []):
            print(f"  {s.get('time_start')}-{s.get('time_end')}  {names.get(s.get('venue_id'), '???')}")

    with open("draft_itinerary.json", "w", encoding="utf-8") as f:
        json.dump(itinerary, f, indent=2, ensure_ascii=False)
    print("\nSaved draft_itinerary.json")


if __name__ == "__main__":
    main()