"""
harvest_venues.py - build the Davao City venue dataset from the Google Places API (New).

Usage (PowerShell):
    $env:GOOGLE_MAPS_API_KEY="your-key"
    python harvest_venues.py --max-requests 20   # small test run first
    python harvest_venues.py                     # full run (resumes if raw_places.json exists)
    python harvest_venues.py --use-cache         # NO API calls: rebuild the dataset from raw_places.json
    python harvest_venues.py --fresh             # ignore the cache and start over

Outputs:
    raw_places.json     raw API results + which (query, tile) pairs are done (makes reruns free)
    davao_venues.json   final dataset (load this into Firestore)
    davao_venues.xlsx   same data for eyeballing in Excel
    dropped_venues.csv  everything the filters removed, with the reason
"""
import argparse
import json
import os
import re
import sys
import time

import pandas as pd
import requests

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

RAW_CACHE = "raw_places.json"
OUT_JSON = "davao_venues.json"
OUT_XLSX = "davao_venues.xlsx"
DROPPED_CSV = "dropped_venues.csv"

DEFAULT_MAX_REQUESTS = 500   # hard cap on API requests per run (every page is a billed request)
MIN_REVIEWS = 5              # commercial places (food, shops, hotels, spas) need at least this many reviews
MIN_REVIEWS_ATTRACTION = 2   # nature/culture spots are often remote and have few reviews
ATTRACTION_INTERESTS = {"Nature", "Culture", "Entertainment", "Sightseeing"}

# ---------------------------------------------------------------------------
# Geography. (lat, lng). Approximate on purpose: the address filter below
# ("Davao City" in the address) is the real boundary; the rectangles only
# steer the search. Text Search accepts rectangles only, not polygons.
# ---------------------------------------------------------------------------
CITY_RECT = ((6.80, 125.05), (7.55, 125.75))   # whole Davao City, roughly
CORE_RECT = ((6.98, 125.48), (7.18, 125.68))   # dense urban core
CORE_GRID = (2, 2)                             # core is split into rows x cols tiles,
                                               # because one query returns at most 60 results
# Google sometimes returns addresses in Filipino ("Lungsod ng Dabaw") even with languageCode "en".
DAVAO_RE = re.compile(r"\b(Davao City|Lungsod ng Dabaw)\b", re.IGNORECASE)


def split_rect(rect, rows, cols):
    (lat0, lng0), (lat1, lng1) = rect
    dlat, dlng = (lat1 - lat0) / rows, (lng1 - lng0) / cols
    return [((lat0 + r * dlat, lng0 + c * dlng), (lat0 + (r + 1) * dlat, lng0 + (c + 1) * dlng))
            for r in range(rows) for c in range(cols)]


CITY_TILES = [CITY_RECT]
CORE_TILES = split_rect(CORE_RECT, *CORE_GRID)

# ---------------------------------------------------------------------------
# What to search for: (query text, interest hint, area).
# "city" = whole-city rectangle (few results, sparse places).
# "core" = urban core split into tiles (dense categories).
# The hint is only used when Google's own place types don't map to an interest.
# City-wide queries run first so a small test run covers a variety of places.
# ---------------------------------------------------------------------------
QUERIES = [
    ("waterfall Davao City", "Nature", "city"),
    ("beach Davao City", "Nature", "city"),
    ("nature park Davao City", "Nature", "city"),
    ("eco park Davao City", "Nature", "city"),
    ("hiking trail Davao City", "Nature", "city"),
    ("farm tourism Davao City", "Nature", "city"),
    ("botanical garden Davao City", "Nature", "city"),
    ("museum Davao City", "Culture", "city"),
    ("historic landmark Davao City", "Culture", "city"),
    ("church Davao City", "Culture", "city"),
    ("temple Davao City", "Culture", "city"),
    ("art gallery Davao City", "Culture", "city"),
    ("indigenous cultural village Davao City", "Culture", "city"),
    ("zoo Davao City", "Entertainment", "city"),
    ("amusement park Davao City", "Entertainment", "city"),
    ("adventure park zipline Davao City", "Entertainment", "city"),
    ("water park Davao City", "Entertainment", "city"),
    ("tourist attraction Davao City", "Sightseeing", "city"),
    ("things to do Davao City", "Sightseeing", "city"),
    ("resort Davao City", "Accommodation", "city"),
    ("restaurant Davao City", "Food", "core"),
    ("seafood restaurant Davao City", "Food", "core"),
    ("Filipino restaurant Davao City", "Food", "core"),
    ("tuna panga Davao City", "Food", "core"),
    ("durian Davao City", "Food", "core"),
    ("cafe Davao City", "Food", "core"),
    ("bakery Davao City", "Food", "core"),
    ("fine dining Davao City", "Food", "core"),
    ("street food Davao City", "Food", "core"),
    ("hotel Davao City", "Accommodation", "core"),
    ("boutique hotel Davao City", "Accommodation", "core"),
    ("hostel Davao City", "Accommodation", "core"),
    ("shopping mall Davao City", "Shopping", "core"),
    ("souvenir shop pasalubong Davao City", "Shopping", "core"),
    ("public market Davao City", "Shopping", "core"),
    ("spa Davao City", "Wellness", "core"),
    ("massage Davao City", "Wellness", "core"),
]

# ---------------------------------------------------------------------------
# Interest taxonomy: Google place types -> your interest categories. EDIT FREELY.
# (Type names that Google doesn't use are harmless: they just never match.)
# ---------------------------------------------------------------------------
TYPE_TO_INTEREST = {
    "Food": {"restaurant", "cafe", "coffee_shop", "bakery", "bar", "pub", "ice_cream_shop",
             "dessert_shop", "food_court", "fast_food_restaurant", "wine_bar"},
    "Nature": {"park", "city_park", "national_park", "state_park", "hiking_area", "beach", "nature_preserve",
               "scenic_spot", "botanical_garden", "garden", "island", "lake", "river",
               "mountain_peak", "woods", "farm", "waterfall"},
    "Culture": {"museum", "art_gallery", "art_museum", "history_museum", "cultural_landmark",
                "historical_place", "historical_landmark", "sculpture", "monument", "church",
                "place_of_worship", "mosque",
                "hindu_temple", "buddhist_temple", "cultural_center", "performing_arts_theater"},
    "Entertainment": {"amusement_park", "zoo", "aquarium", "water_park", "theme_park",
                      "amusement_center", "adventure_sports_center", "movie_theater", "night_club"},
    "Shopping": {"shopping_mall", "department_store", "market", "gift_shop", "souvenir_store"},
    "Wellness": {"spa", "massage", "massage_spa", "wellness_center", "sauna", "yoga_studio"},
    "Accommodation": {"hotel", "lodging", "resort_hotel", "hostel", "guest_house",
                      "bed_and_breakfast", "inn", "motel", "extended_stay_hotel", "campground"},
    "Sightseeing": {"tourist_attraction", "landmark", "observation_deck"},
}

# Places whose PRIMARY type is one of these are not visitable stops: dropped.
EXCLUDED_PRIMARY_TYPES = {
    "travel_agency", "real_estate_agency", "school", "primary_school", "secondary_school",
    "university", "college", "hospital", "doctor", "dentist", "pharmacy", "bank", "atm",
    "gas_station", "car_rental", "car_repair", "car_dealer", "insurance_agency", "lawyer",
    "funeral_home", "cemetery", "corporate_office", "government_office",
    "local_government_office", "post_office", "storage", "moving_company", "courier_service",
    "laundry", "hardware_store", "convenience_store", "grocery_store", "supermarket",
    "beauty_salon", "hair_salon", "hair_care", "nail_salon", "barber_shop",
}

# ---------------------------------------------------------------------------
# Price. Google's priceLevel -> tier (1-4) and readable label.
# When Google has no priceLevel, a CATEGORY DEFAULT is used and flagged with
# price_source = "category_default". These defaults are FIRST-PASS ASSUMPTIONS:
# review them, and hand-verify the entrance fees of your most common attractions.
# ---------------------------------------------------------------------------
PRICE_TIER = {"PRICE_LEVEL_FREE": 1, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
              "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
PRICE_LABEL = {"PRICE_LEVEL_FREE": "Free", "PRICE_LEVEL_INEXPENSIVE": "Inexpensive",
               "PRICE_LEVEL_MODERATE": "Moderate", "PRICE_LEVEL_EXPENSIVE": "Expensive",
               "PRICE_LEVEL_VERY_EXPENSIVE": "Very Expensive"}

FREE, CHEAP, MOD, EXP = ("PRICE_LEVEL_FREE", "PRICE_LEVEL_INEXPENSIVE",
                         "PRICE_LEVEL_MODERATE", "PRICE_LEVEL_EXPENSIVE")
DEFAULT_PRICE_RULES = [  # first matching rule wins
    ({"resort_hotel"}, EXP),
    ({"hostel", "guest_house", "bed_and_breakfast", "inn", "motel", "campground"}, CHEAP),
    ({"hotel", "lodging", "extended_stay_hotel"}, MOD),
    ({"spa", "massage", "massage_spa", "wellness_center", "sauna"}, MOD),
    ({"zoo", "aquarium", "amusement_park", "water_park", "theme_park", "adventure_sports_center"}, MOD),
    ({"museum", "art_gallery", "art_museum", "history_museum", "botanical_garden", "hiking_area",
      "national_park", "nature_preserve", "beach", "farm"}, CHEAP),
    ({"park", "scenic_spot", "monument", "church", "place_of_worship", "mosque", "cultural_landmark",
      "historical_place", "observation_deck"}, FREE),
    ({"cafe", "coffee_shop", "bakery", "ice_cream_shop", "dessert_shop"}, CHEAP),
    ({"restaurant", "bar", "pub", "food_court"}, MOD),
    ({"shopping_mall", "department_store", "market", "gift_shop", "souvenir_store"}, CHEAP),
]


def default_price_level(types):
    tset = set(types)
    for typeset, level in DEFAULT_PRICE_RULES:
        if tset & typeset:
            return level
    if any(t.endswith("_restaurant") for t in types):
        return MOD
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
GOOGLE_DAY = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]   # Google: 0 = Sunday


def parse_hours(roh):
    """regularOpeningHours -> {"mon": "08:00-17:00", ...}. Closed days = "closed".
    Overnight ranges (e.g. "22:00-02:00") are kept as-is: your validator must handle them."""
    if not roh:
        return None
    periods = roh.get("periods") or []
    if not periods:
        return None
    if len(periods) == 1 and "close" not in periods[0]:      # Google's encoding of open 24/7
        return {d: "00:00-23:59" for d in DAY_ORDER}
    out = {d: [] for d in DAY_ORDER}
    for p in periods:
        o, c = p.get("open"), p.get("close")
        if not o or not c:
            continue
        day = GOOGLE_DAY[o.get("day", 0) % 7]
        out[day].append(f"{o.get('hour', 0):02d}:{o.get('minute', 0):02d}-"
                        f"{c.get('hour', 0):02d}:{c.get('minute', 0):02d}")
    return {d: (", ".join(v) if v else "closed") for d, v in out.items()}


def price_range_text(pr):
    """Raw Google priceRange kept as text for reference only (tiers stay the scoring field)."""
    if not pr:
        return None

    def fmt(p):
        return f"{p.get('currencyCode', '')} {p.get('units', '0')}".strip() if p else None

    s, e = fmt(pr.get("startPrice")), fmt(pr.get("endPrice"))
    return f"{s} - {e}" if s and e else (s or e)


# For places where Google gave NO primary type at all (e.g. many beaches), the name is checked
# against these keywords, per the query that found them. Everything else with no recognizable
# type is dropped and listed in dropped_venues.csv.
NAME_RESCUE = {
    "Nature": re.compile(r"\b(beach|aplaya|resort|cove|falls?|waterfall|trail|peak|mt\.?|mount|"
                         r"eco[- ]?park|nature|garden|farm|river|lake|island|hills?|park)\b", re.I),
    "Accommodation": re.compile(r"\b(resort|hotel|inn|lodge|villa|suites?|hostel|pension)\b", re.I),
    "Culture": re.compile(r"\b(museum|church|shrine|temple|cathedral|gallery|heritage|landmark)\b", re.I),
    "Entertainment": re.compile(r"\b(park|zoo|adventure|zipline|water ?park)\b", re.I),
    "Wellness": re.compile(r"\b(spa|massage|wellness)\b", re.I),
    "Food": re.compile(r"\b(restaurant|cafe|coffee|grill|bakery|eatery|kitchen)\b", re.I),
    "Shopping": re.compile(r"\b(mall|market|souvenir|pasalubong|shop)\b", re.I),
}


def interests_for(types, hints, name, primary):
    out = [i for i, tset in TYPE_TO_INTEREST.items() if set(types) & tset]
    if "Food" not in out and any(t.endswith("_restaurant") for t in types):
        out.append("Food")
    # A query hint refines generic attractions (e.g. a waterfall that Google types as just
    # "tourist_attraction")...
    refine = next((h for h in hints if h != "Sightseeing"), None)
    if out == ["Sightseeing"] and refine:
        out.insert(0, refine)
    # ...and a name keyword can rescue a place Google left without any primary type.
    if not out and not primary:
        for h in hints:
            rx = NAME_RESCUE.get(h)
            if rx and rx.search(name or ""):
                out = [h]
                break
    return out


# ---------------------------------------------------------------------------
# API harvesting
# ---------------------------------------------------------------------------
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.types", "places.primaryType", "places.businessStatus", "places.rating",
    "places.userRatingCount", "places.priceLevel", "places.priceRange",
    "places.regularOpeningHours", "nextPageToken",
])


def search(query, tile, state):
    """Run one query in one rectangle, following pagination (max 3 pages = 60 results).
    Returns (places, completed_ok)."""
    (lat0, lng0), (lat1, lng1) = tile
    body = {
        "textQuery": query, "pageSize": 20, "languageCode": "en", "regionCode": "PH",
        "locationRestriction": {"rectangle": {"low": {"latitude": lat0, "longitude": lng0},
                                              "high": {"latitude": lat1, "longitude": lng1}}},
    }
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": API_KEY,
               "X-Goog-FieldMask": FIELD_MASK}
    places = []
    for _ in range(3):
        if state["requests"] >= state["max_requests"]:
            state["stopped"] = True
            return places, False
        resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=30)
        state["requests"] += 1
        if resp.status_code in (401, 403):
            print(f"\nAPI refused the request ({resp.status_code}). Check the key, that Places API (New) "
                  f"is enabled, and that billing is on:\n{resp.text[:400]}")
            sys.exit(1)
        if resp.status_code != 200:
            print(f"  ! {resp.status_code} for '{query}': {resp.text[:200]}")
            return places, False
        data = resp.json()
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(0.3)
    return places, True


def harvest(raw, max_requests):
    state = {"requests": 0, "max_requests": max_requests, "stopped": False}
    jobs = [(q, hint, area, i, tile)
            for q, hint, area in QUERIES
            for i, tile in enumerate(CITY_TILES if area == "city" else CORE_TILES)]
    todo = [j for j in jobs if f"{j[0]}|{j[3]}" not in raw["done"]]
    print(f"{len(jobs)} query/tile jobs, {len(jobs) - len(todo)} already done, {len(todo)} to run "
          f"(cap: {max_requests} requests)")
    try:
        for n, (q, hint, area, i, tile) in enumerate(todo, 1):
            places, ok = search(q, tile, state)
            for p in places:
                entry = raw["places"].setdefault(p["id"], p)
                hints = entry.setdefault("_hints", [])
                if hint and hint not in hints:
                    hints.append(hint)
            if ok:
                raw["done"].append(f"{q}|{i}")
            print(f"  [{n}/{len(todo)}] {q} (tile {i}): {len(places)} results, "
                  f"{state['requests']} requests so far")
            if state["stopped"]:
                print("Request cap reached - stopping. Rerun to continue where this left off.")
                break
    finally:
        with open(RAW_CACHE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
    print(f"API requests used this run: {state['requests']}")


# ---------------------------------------------------------------------------
# Build the dataset from the raw results
# ---------------------------------------------------------------------------
def build_dataset(raw_places):
    kept, dropped = [], []
    for pid, p in raw_places.items():
        name = (p.get("displayName") or {}).get("text", "")
        addr = p.get("formattedAddress", "")
        types = p.get("types", [])
        primary = p.get("primaryType")
        hints = p.get("_hints", [])
        interests = interests_for(types, hints, name, primary)
        min_reviews = (MIN_REVIEWS_ATTRACTION if interests and set(interests) <= ATTRACTION_INTERESTS
                       else MIN_REVIEWS)
        reason = None
        if p.get("businessStatus") not in (None, "OPERATIONAL"):
            reason = "not_operational"
        elif not DAVAO_RE.search(addr):
            reason = "outside_davao_city"
        elif primary in EXCLUDED_PRIMARY_TYPES:
            reason = f"excluded_type:{primary}"
        elif not interests:
            reason = "no_interest_category"
        elif (p.get("userRatingCount") or 0) < min_reviews:
            reason = "few_reviews"
        if reason:
            dropped.append({"place_id": pid, "name": name, "address": addr,
                            "primary_type": primary, "reason": reason})
            continue

        raw_level = p.get("priceLevel")
        if raw_level in PRICE_TIER:
            source = "google"
        else:
            raw_level = default_price_level(types)
            source = "category_default" if raw_level else None
        loc = p.get("location") or {}
        kept.append({
            "venue_id": None,
            "place_id": pid,
            "name": name,
            "category": interests,
            "primary_type": primary,
            "google_types": types,
            "address": addr,
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "price_tier": PRICE_TIER.get(raw_level),
            "price_category": PRICE_LABEL.get(raw_level, "Not Available"),
            "price_source": source,
            "price_range_text": price_range_text(p.get("priceRange")),
            "average_rating": p.get("rating"),
            "rating_count": p.get("userRatingCount"),
            "operating_hours": parse_hours(p.get("regularOpeningHours")),
        })
    kept.sort(key=lambda v: v["place_id"])
    for i, v in enumerate(kept, 1):
        v["venue_id"] = f"dvo_poi_{i:04d}"
    return kept, dropped


def write_outputs(kept, dropped):
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)
    flat = pd.DataFrame(kept)
    for col in ("category", "google_types", "operating_hours"):
        flat[col] = flat[col].apply(lambda v: json.dumps(v, ensure_ascii=False) if v is not None else None)
    flat.to_excel(OUT_XLSX, index=False)
    pd.DataFrame(dropped).to_csv(DROPPED_CSV, index=False, encoding="utf-8-sig")


def print_summary(kept, dropped):
    df = pd.DataFrame(kept)
    print(f"\n=== Dataset: {len(kept)} venues kept, {len(dropped)} dropped ===")
    if dropped:
        print("Dropped by reason:")
        print(pd.DataFrame(dropped)["reason"].value_counts().to_string())
    if kept:
        print("\nVenues per interest category (a venue can count in several):")
        print(df["category"].explode().value_counts().to_string())
        print("\nprice_source:")
        print(df["price_source"].fillna("none").value_counts().to_string())
        print(f"\nHave opening hours: {df['operating_hours'].notna().sum()}/{len(df)}")
        print(f"Have rating:        {df['average_rating'].notna().sum()}/{len(df)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-cache", action="store_true", help="no API calls; rebuild from raw_places.json")
    ap.add_argument("--fresh", action="store_true", help="ignore any existing cache")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    args = ap.parse_args()

    raw = {"places": {}, "done": []}
    if os.path.exists(RAW_CACHE) and not args.fresh:
        with open(RAW_CACHE, encoding="utf-8") as f:
            raw = json.load(f)
        print(f"Loaded cache: {len(raw['places'])} places, {len(raw['done'])} jobs done")
    elif args.use_cache:
        sys.exit(f"--use-cache given but {RAW_CACHE} does not exist")

    if not args.use_cache:
        if not API_KEY:
            sys.exit("Set the GOOGLE_MAPS_API_KEY environment variable first")
        harvest(raw, args.max_requests)

    kept, dropped = build_dataset(raw["places"])
    write_outputs(kept, dropped)
    print_summary(kept, dropped)
    print(f"\nWrote {OUT_JSON}, {OUT_XLSX}, {DROPPED_CSV}")


if __name__ == "__main__":
    main()