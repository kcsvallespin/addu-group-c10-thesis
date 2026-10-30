"""
load_venues.py - load benchmark_venues.json (the price_data.xlsx venues, from make_price_overrides.py) into Firestore.

Usage (PowerShell):
    python load_venues.py                          # writes to the "venues_test" collection
    python load_venues.py --collection venues      # writes to the real "venues" collection
    python load_venues.py --collection venues --prune   # also DELETES documents that are not in the dataset
                                                         # (e.g. the 1,394 venues uploaded before)

Auth: uses your `gcloud auth application-default login` credentials (no key file).
"""
import argparse
import json
import os

import firebase_admin
from firebase_admin import firestore

PROJECT_ID = "davao-itinerary-thesis"   # Firebase project ID
INPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_venues.json")

ap = argparse.ArgumentParser()
ap.add_argument("--collection", default="venues_test")
ap.add_argument("--prune", action="store_true", help="delete documents whose id is not in the dataset")
args = ap.parse_args()

with open(INPUT, encoding="utf-8") as f:
    venues = json.load(f)

firebase_admin.initialize_app(options={"projectId": PROJECT_ID})
db = firestore.client()
collection = db.collection(args.collection)

batch = db.batch()
for i, venue in enumerate(venues, 1):
    batch.set(collection.document(venue["venue_id"]), venue)   # lists/dicts/None are stored natively
    if i % 400 == 0:                                           # Firestore allows 500 writes per batch
        batch.commit()
        batch = db.batch()
batch.commit()
print(f"Uploaded {len(venues)} venues to '{args.collection}'.")

if args.prune:
    keep = {v["venue_id"] for v in venues}
    stale = [doc.reference for doc in collection.stream() if doc.id not in keep]
    for i in range(0, len(stale), 400):
        batch = db.batch()
        for ref in stale[i:i + 400]:
            batch.delete(ref)
        batch.commit()
    print(f"Deleted {len(stale)} document(s) not in the dataset from '{args.collection}'.")
