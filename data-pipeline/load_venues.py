import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

# Step 1
firebase_admin.initialize_app(options={'projectId': 'davao-itinerary-thesis'})
db = firestore.client()

# Step 2
df = pd.read_excel("davao_venues_TEST_clean.xlsx")

def safe_int(val):
    return int(val) if pd.notna(val) else None

def safe_float(val):
    return float(val) if pd.notna(val) else None

batch = db.batch()
collection = db.collection("venues_test")

for i, row in df.iterrows():
    doc_ref = collection.document(row["venue_id"])
    batch.set(doc_ref, {
        "name": row["name"],
        "category": row["category"],
        "address": row["address"],
        "latitude": safe_float(row["latitude"]),
        "longitude": safe_float(row["longitude"]),
        "price_tier": safe_int(row["price_tier"]),
        "estimated_cost_php": safe_float(row["estimated_cost_php"]),
        "average_rating": safe_float(row["average_rating"]),
        "operating_hours": row["operating_hours"] if pd.notna(row["operating_hours"]) else None,
    })
    if (i + 1) % 500 == 0:
        batch.commit()
        batch = db.batch()

batch.commit()
print(f"Uploaded {len(df)} venues.")