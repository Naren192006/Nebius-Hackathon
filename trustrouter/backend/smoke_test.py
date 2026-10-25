"""Day 1, step 2: one tiny prompt to each tier. Prints text, tokens, latency, cost."""
from llm import TIERS, call_llm

PROMPT = [{"role": "user", "content": "In one short sentence, what is a GPU?"}]

for tier in TIERS:
    r = call_llm(tier, PROMPT, max_tokens=120)
    print(f"[{tier}] {r.model}")
    print(f"  answer : {r.text[:200]}")
    print(f"  tokens : {r.tokens_in} in / {r.tokens_out} out")
    print(f"  latency: {r.latency_ms} ms   cost: ${r.cost_usd:.6f}\n")
