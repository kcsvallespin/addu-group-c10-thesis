import pandas as pd
import re

print("Script started")

df = pd.read_excel("davao_venues.xlsx", sheet_name="ESTABLISHMENTS", header=3)
df = df.dropna(subset=["Name of Establishment"]).reset_index(drop=True)

print(len(df))

df["venue_id"] = [f"dvo_poi_{i+1:03}" for i in range(len(df))]
df["name"] = df["Name of Establishment"]
df["category"] = df["Enterrpise Type"].apply(lambda x: [x])  # e.g. ["Hotel"] — expand later
df["address"] = df["Address"]

# Placeholders — filled in by the Geocoding/Places API enrichment step
df["latitude"] = None
df["longitude"] = None
df["price_tier"] = None
df["estimated_cost_php"] = None
df["average_rating"] = None
df["operating_hours"] = None

df_clean = df[["venue_id", "name", "category", "address", "latitude", "longitude",
               "price_tier", "estimated_cost_php", "average_rating", "operating_hours"]]

df_clean.to_excel("davao_venues_clean.xlsx", index=False)
print(df_clean)