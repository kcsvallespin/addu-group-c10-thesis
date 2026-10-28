import os
import requests

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    raise ValueError("Set OPENROUTER_API_KEY environment variable first")

response = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": "openrouter/hunter-alpha",
        "messages": [
            {"role": "user", "content": "Say hello in one sentence."}
        ],
    },
)

print(f"Status code: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    reply = data["choices"][0]["message"]["content"]
    print(f"Hunter Alpha says: {reply}")
else:
    print("Request failed. Full response below:")
    print(response.text)