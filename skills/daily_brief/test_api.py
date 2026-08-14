import os, requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(r"C:\Users\GilesHugo\.hermes\.env"))
key = os.environ.get("ANTHROPIC_API_KEY", "")
print(f"Key starts: {key[:20]}...")
print(f"Key length: {len(key)}")

resp = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
    timeout=30,
)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
