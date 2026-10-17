"""Label rubric for bench/trace.jsonl.

Written before the harness was run. Do not retune min_tier or omo_category
to improve a score. bench/README.md is the prose form of these rules.
"""

TIER_BY_DIFFICULTY = {1: "fast", 2: "fast", 3: "balanced", 4: "balanced", 5: "deep"}
TIER_RANK = {"fast": 0, "balanced": 1, "deep": 2}

# Judgment calls, fixed here:
# - docs -> writing. OmO writing names only Google and Anthropic models.
# - review -> unspecified-high. OmO has no review category; this is the broad lane.
# - audit and architecture -> ultrabrain.
# - difficulty <= 2 -> quick, for every other type.
# - difficulty 3 -> unspecified-low.
# - difficulty 4 -> unspecified-high.
# - difficulty 5 debug -> deep-high (the cause is treated as not settleable from one read).
# - other difficulty 5 -> deep-low (OmO's default deep lane).
# research is a large-context read. openswarm has no special case for it.


def min_tier(type_, difficulty):
    if type_ == "architecture":
        return "deep"
    return TIER_BY_DIFFICULTY[int(difficulty)]


def omo_category(type_, difficulty):
    difficulty = int(difficulty)
    if type_ == "docs":
        return "writing"
    if type_ in ("audit", "architecture"):
        return "ultrabrain"
    if type_ == "review":
        return "unspecified-high"
    if difficulty <= 2:
        return "quick"
    if difficulty == 3:
        return "unspecified-low"
    if difficulty == 4:
        return "unspecified-high"
    if type_ == "debug":
        return "deep-high"
    return "deep-low"


def independent_review(type_):
    return type_ in ("review", "audit")


def context_tokens(type_, difficulty, large=None):
    if large:
        return int(large)
    return {"fast": 8000, "balanced": 24000, "deep": 48000}[min_tier(type_, difficulty)]


def token_estimate(type_, difficulty, context):
    """Deterministic input/output estimates. Not measured usage."""
    tier = min_tier(type_, difficulty)
    inp = {"fast": 8000, "balanced": 22000, "deep": 64000}[tier]
    out = {"fast": 800, "balanced": 3000, "deep": 9000}[tier]
    if context >= 220000:
        inp = int(context)
        out = 1500
    if type_ in ("review", "audit"):
        inp = max(inp, 36000)
        out = max(out, 4000)
    return inp, out


def label_task(spec):
    type_ = spec["type"]
    difficulty = int(spec["difficulty"])
    context = context_tokens(type_, difficulty, spec.get("context_tokens"))
    inp, out = token_estimate(type_, difficulty, context)
    row = {
        "id": spec["id"],
        "title": spec["title"],
        "type": type_,
        "difficulty": difficulty,
        "context_tokens": context,
        "omo_category": omo_category(type_, difficulty),
        "input_tokens": inp,
        "output_tokens": out,
        "min_tier": min_tier(type_, difficulty),
        "independent_review": independent_review(type_),
    }
    return row
