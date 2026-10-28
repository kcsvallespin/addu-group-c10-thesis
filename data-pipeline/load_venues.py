"""
load_venues.py - load davao_venues.json (from harvest_venues.py) into Firestore.

Usage (PowerShell):
    python load_venues.py                          # writes to the "venues_test" collection
    python load_venues.py --collection venues      # writes to the real "venues" collection

Auth: uses your `gcloud auth application-default login` credentials (no key file).
"""
import argparse
import json

import firebase_admin
from firebase_admin import firestore

PROJECT_ID = "davao-itinerary-thesis"   # Firebase project ID
INPUT = "davao_venues.json"

ap = argparse.ArgumentParser()
ap.add_argument("--collection", default="venues_test")
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