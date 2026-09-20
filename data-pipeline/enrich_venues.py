import pandas as pd
import requests
import os
import time
 
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    raise ValueError("Set GOOGLE_MAPS_API_KEY environment variable first")
 
# Maps Google's raw priceLevel enum to a numeric 1-4 tier
# (used for sorting/filtering) and a readable label
# (used for display / reporting).
PRICE_TIER_MAP = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
 
PRICE_LABEL_MAP = {
    "PRICE_LEVEL_FREE": "Free",
    "PRICE_LEVEL_INEXPENSIVE": "Inexpensive",
    "PRICE_LEVEL_MODERATE": "Moderate",
    "PRICE_LEVEL_EXPENSIVE": "Expensive",
    "PRICE_LEVEL_VERY_EXPENSIVE": "Very Expensive",
}
 
 
def geocode_address(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": API_KEY}
    resp = requests.get(url, params=params).json()
    if resp["status"] == "OK":
        loc = resp["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    print(f"  Geocoding failed for '{address}': {resp['status']}")
    return None, None
 
 
def find_place_id(name, address):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.id",
    }
    body = {"textQuery": f"{name}, {address}"}
    resp = requests.post(url, headers=headers, json=body).json()
    places = resp.get("places", [])
    if places:
        return places[0]["id"]
    print(f"  No place found for '{name}'")
    return None
 
 
def get_place_details(place_id):
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "priceLevel,rating,regularOpeningHours",
    }
    resp = requests.get(url, headers=headers).json()
    raw_price_level = resp.get("priceLevel")  # e.g. "PRICE_LEVEL_MODERATE"
    rating = resp.get("rating")
    hours = resp.get("regularOpeningHours", {}).get("weekdayDescriptions")
    return raw_price_level, rating, hours
 
 
def main():
    df = pd.read_excel("davao_venues_clean.xlsx")

    # price_category replaces estimated_cost_php (Option A: readable label,
    # not a peso figure). Add the column if it isn't already present.
    if "price_category" not in df.columns:
        df["price_category"] = None
    if "estimated_cost_php" in df.columns:
        df = df.drop(columns=["estimated_cost_php"])

    for col in ["price_tier", "price_category", "average_rating", "operating_hours"]:
        df[col] = df[col].astype(object)

    for i, row in df.iterrows():
        print(f"Enriching: {row['name']}")

        lat, lng = geocode_address(row["address"])
        df.at[i, "latitude"] = lat
        df.at[i, "longitude"] = lng

        place_id = find_place_id(row["name"], row["address"])
        if place_id:
            raw_price_level, rating, hours = get_place_details(place_id)
            print(f"  raw_price_level: {raw_price_level}, rating: {rating}")
            df.at[i, "price_tier"] = PRICE_TIER_MAP.get(raw_price_level)
            df.at[i, "price_category"] = PRICE_LABEL_MAP.get(raw_price_level, "Not Available")
            df.at[i, "average_rating"] = rating
            df.at[i, "operating_hours"] = str(hours) if hours else None

        time.sleep(0.2)


    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", None)
    df.to_excel("davao_venues_enriched.xlsx", index=False)
    print(df)
 
 
if __name__ == "__main__":
    main()