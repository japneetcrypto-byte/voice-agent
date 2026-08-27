import os
from dotenv import load_dotenv
load_dotenv()

print("=== KEY STATUS ===")
for i, name in enumerate(["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"], 1):
    val = os.getenv(name, "")
    if val and not val.startswith(("your_", "<<<")):
        print(f"  key {i}: SET, len={len(val)}, starts={val[:8]}...")
    else:
        print(f"  key {i}: NOT SET")

print("\n=== TESTING EACH KEY with correct model ===")
from google import genai
from google.genai import types

for i, name in enumerate(["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"], 1):
    val = os.getenv(name, "")
    if not val or val.startswith(("your_", "<<<")):
        continue
    print(f"\nTesting key {i} ({name})...")
    try:
        client = genai.Client(api_key=val)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents="Say hello in one word",
        )
        print(f"  ✅ SUCCESS: {response.text[:50]}")
    except Exception as e:
        err = str(e)[:300]
        if "429" in err:
            print(f"  ❌ 429 QUOTA EXHAUSTED")
        else:
            print(f"  ❌ ERROR: {err}")
