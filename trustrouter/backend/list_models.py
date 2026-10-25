"""Day 1, step 1: print the Nemotron model IDs available to your account."""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url=os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
    api_key=os.environ["NEBIUS_API_KEY"],
)

ids = sorted(m.id for m in client.models.list().data)
nemotron = [i for i in ids if "nemotron" in i.lower()]
print("Nemotron models:" if nemotron else "No 'nemotron' IDs found; all models:")
for i in nemotron or ids:
    print(" ", i)
print("\nPaste the Nano / Super / Ultra IDs into .env as MODEL_NANO / MODEL_SUPER / MODEL_ULTRA")
