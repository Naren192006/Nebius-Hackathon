"""Load a prompt file and fill {{placeholders}}. Returns chat messages for call_llm."""
from pathlib import Path

HERE = Path(__file__).parent


def build(name: str, **fields) -> list[dict]:
    system, user = (HERE / f"{name}.txt").read_text().split("---USER---", 1)
    for key, value in fields.items():
        system = system.replace("{{" + key + "}}", str(value))
        user = user.replace("{{" + key + "}}", str(value))
    return [{"role": "system", "content": system.strip()},
            {"role": "user", "content": user.strip()}]


def claims_block(claims: list[str], evidence: list[list[dict]]) -> str:
    """evidence[i] is a list of {"url":..., "snippet":...} for claims[i]."""
    blocks = []
    for i, (claim, ev) in enumerate(zip(claims, evidence)):
        lines = [f"[{i}] CLAIM: {claim}"]
        lines += [f"    E{j} ({e['url']}): {e['snippet']}" for j, e in enumerate(ev)] or ["    (no evidence found)"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
