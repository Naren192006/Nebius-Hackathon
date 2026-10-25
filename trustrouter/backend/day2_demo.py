"""Day 2: route 10 sample prompts and log each decision.

Default: routing only (costs nothing). Add --call to also send each prompt to its
chosen tier (uses credits, answers are capped at 150 tokens).
"""
import sys

from router import route_prompt
from store import build_record, save_record

SAMPLES = [
    "What is the capital of Australia?",
    "Write a one-sentence greeting for a new teammate.",
    "In what year did Chandrayaan-3 land near the lunar south pole?",
    "Summarize the 2019 paper 'Quantum Gradient Folding for Sparse Transformers' by Elena Marchetti.",
    "Why did Isaac Newton win the Nobel Prize in Physics?",
    "A train travels 120 km in 1.5 hours. What is its average speed? Solve step by step.",
    "Compare the trade-offs of SQL and NoSQL databases and design a schema for a chat app.",
    "Fix this: ```def add(a, b): return a - b```",
    "Who wrote the novel Midnight's Children and when was it published?",
    "Give me three tips for staying focused while studying.",
]

call = "--call" in sys.argv
if call:
    from llm import call_llm, cost_of

for prompt in SAMPLES:
    route = route_prompt(prompt)
    print(f"{route['model']:<6} conf={route['confidence']:.2f}  {prompt[:60]}")
    print(f"         why: {route['reason']}")
    if call:
        res = call_llm(route["model"], [{"role": "user", "content": prompt}], max_tokens=150)
        baseline = cost_of("ultra", res.tokens_in, res.tokens_out)
        rec = build_record(prompt, route, res, ultra_baseline_estimate=baseline)
        save_record(rec)
        print(f"         saved {rec['request_id'][:8]}  {res.latency_ms} ms  ${res.cost_usd:.6f}")
