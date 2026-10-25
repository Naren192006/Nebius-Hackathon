"""Print Nemotron model IDs available on Nebius Token Factory.

Usage:
    python list_models.py            # Nemotron rows only
    python list_models.py --all      # full catalog

The live catalog is the authority for the IDs in .env.

Requires NEBIUS_API_KEY (in .env or the environment). No credits are spent:
models.list() is a metadata call.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
TIER_VARS = ("MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA")


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not api_key:
        print("NEBIUS_API_KEY is not set. Add it to backend/.env first.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="print the entire catalog")
    args = parser.parse_args()

    client = OpenAI(api_key=api_key, base_url=os.environ.get("NEBIUS_BASE_URL", BASE_URL))
    try:
        models = client.models.list()
    except Exception as exc:  # noqa: BLE001 - surface any auth/network failure to the user
        print(f"models.list() failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    ids = sorted(model.id for model in models.data)
    if args.all:
        for model_id in ids:
            print(model_id)
        return 0

    nemotron = [model_id for model_id in ids if "nemotron" in model_id.lower()]
    if not nemotron:
        print("No Nemotron IDs found in the catalog. Try --all.", file=sys.stderr)
        return 3

    for model_id in nemotron:
        print(model_id)

    print("\nConfigured tiers (.env):")
    configured = {os.environ.get(name, "") for name in TIER_VARS}
    for name in TIER_VARS:
        value = os.environ.get(name, "")
        state = "ok" if value in nemotron else "NOT IN CATALOG - update .env"
        print(f"  {name:12} {value or '(unset)':45} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
