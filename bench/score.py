"""Score routing decisions. Cost figures are table × estimated tokens, not bills."""
import csv
import json
import os
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
RANK = {"fast": 0, "balanced": 1, "deep": 2}

BASELINES = [
    {
        "tool": "baseline_codex",
        "model": "gpt-6.1-sol",
        "provider": "chatgpt-subscription",
        "backend": "codex",
        "variant": "medium",
        "note": "Codex balanced workhorse, the fresh-quota default for difficulty 3",
    },
    {
        "tool": "baseline_strongest",
        "model": "gpt-6-astra",
        "provider": "chatgpt-subscription",
        "backend": "codex",
        "variant": "high",
        "note": "Frontier Codex model (deep tier)",
    },
    {
        "tool": "baseline_cheapest",
        "model": "glm-5.3-flash",
        "provider": "opencode-go",
        "backend": "ocgo",
        "variant": "low",
        "note": "Lowest per-token accounting price in the shared table (OpenCode Go cheap-flash band)",
    },
]


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    rows = []
    with open(path) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def worst_codex(scenario):
    return max(scenario["codex"]["five_hour"], scenario["codex"]["seven_day"])


def ocgo_max(scenario, model):
    usage = (scenario.get("ocgo_usage") or {}).get(model) or {}
    return max([usage.get("5h", 0), usage.get("7d", 0), usage.get("30d", 0)] or [0])


def baseline_exhausted(spec, scenario):
    """Same runtime error a naive call would hit. Soft pressure is not an error."""
    pool = spec["quota_pool"]
    if pool == "codex":
        return worst_codex(scenario) >= 0.95
    if pool == "ocgo":
        return ocgo_max(scenario, spec["id"]) >= 1.0
    if pool == "deepseek":
        return scenario["deepseek_balance_cny"] < 10
    return False


def baseline_rows(catalogue, scenarios, trace):
    by_id = {m["id"]: m for m in catalogue["models"]}
    rows = []
    for spec_b in BASELINES:
        model = by_id[spec_b["model"]]
        for scenario in scenarios["scenarios"]:
            for task in trace:
                failed = baseline_exhausted(model, scenario)
                if failed:
                    rows.append({
                        "task_id": task["id"],
                        "scenario": scenario["id"],
                        "tool": spec_b["tool"],
                        "backend": None,
                        "provider": None,
                        "model": None,
                        "effort": None,
                        "variant": None,
                        "tier": None,
                        "attempts": 1,
                        "failed_attempts": 1,
                        "reasons": [
                            f"naive baseline {spec_b['model']} on exhausted quota; no fallback",
                            spec_b["note"],
                        ],
                    })
                else:
                    rows.append({
                        "task_id": task["id"],
                        "scenario": scenario["id"],
                        "tool": spec_b["tool"],
                        "backend": spec_b["backend"],
                        "provider": spec_b["provider"],
                        "model": spec_b["model"],
                        "effort": spec_b["variant"],
                        "variant": spec_b["variant"],
                        "tier": model["tier"],
                        "attempts": 1,
                        "failed_attempts": 0,
                        "reasons": [spec_b["note"]],
                    })
    return rows


def token_cost(cin, cout, inp, out):
    return (inp / 1_000_000.0) * cin + (out / 1_000_000.0) * cout


def price_for(model, prices):
    if model["billing"] == "metered":
        p = prices["deepseek"]["models"][model["id"]]
        return p["cost_in"], p["cost_out"]
    if model["quota_pool"] == "ocgo":
        p = prices["ocgo"]["bands"][model["cost_band"]]
        return p["cost_in"], p["cost_out"]
    return None, None


def adequacy(actual, minimum):
    if actual not in RANK:
        return "under"
    if RANK[actual] == RANK[minimum]:
        return "exact"
    if RANK[actual] > RANK[minimum]:
        return "over"
    return "under"


def reference_family(rows, tasks, families):
    """Family of the first difficulty-3 impl with a modest context, in trace order."""
    ids = [t["id"] for t in tasks if t["type"] == "impl" and t["difficulty"] == 3 and t["context_tokens"] < 220000]
    if not ids:
        return None, None
    ref_id = ids[0]
    for row in rows:
        if row["task_id"] == ref_id and row.get("model"):
            return families.get(row["model"]), row["model"]
    return None, None


def rate(n, d):
    if not d:
        return None
    return round(n / d, 4)


def accumulate(tool, rows, tasks, scenarios, catalogue, prices):
    by_task = {t["id"]: t for t in tasks}
    by_model = {m["id"]: m for m in catalogue["models"]}
    families = {m["id"]: m["family"] for m in catalogue["models"]}
    budget = prices["codex"]["five_hour_token_budget"]
    frac_5h = prices["ocgo"]["window_fractions"]["5h"]
    by_scenario = {}
    for scenario in scenarios["scenarios"]:
        subset = [r for r in rows if r["scenario"] == scenario["id"]]
        by_scenario[scenario["id"]] = score_block(
            tool, scenario["id"], subset, by_task, by_model, families, budget, frac_5h, prices,
        )
    overall = score_block(tool, "ALL", rows, by_task, by_model, families, budget, frac_5h, prices)
    block = {"tool": tool, "overall": overall, "scenarios": by_scenario}
    attach_per_pass(block)
    return block


def score_block(tool, scenario, rows, by_task, by_model, families, budget, frac_5h, prices):
    n = len(rows)
    exact = over = under = unrouted = wasted = failed_sum = 0
    review_n = review_ok = 0
    metered = ocgo_usd = 0.0
    ocgo_share = 0.0
    codex_tokens = 0
    # Reference implementer is per scenario. For ALL, compare each review to its own scenario.
    refs = {}
    if scenario == "ALL":
        for sid in {r["scenario"] for r in rows}:
            fam, model = reference_family([r for r in rows if r["scenario"] == sid], by_task.values(), families)
            refs[sid] = fam
    else:
        fam, model = reference_family(rows, by_task.values(), families)
        refs[scenario] = fam
    for row in rows:
        task = by_task[row["task_id"]]
        kind = adequacy(row.get("tier"), task["min_tier"])
        if kind == "exact":
            exact += 1
        elif kind == "over":
            over += 1
        else:
            under += 1
        if not row.get("model"):
            unrouted += 1
        if row.get("failed_attempts", 0) > 0:
            wasted += 1
        failed_sum += row.get("failed_attempts", 0)
        if task["independent_review"]:
            review_n += 1
            fam = families.get(row.get("model") or "")
            ref = refs.get(row["scenario"])
            if fam and ref and fam != ref:
                review_ok += 1
        model = by_model.get(row.get("model") or "")
        if not model:
            continue
        cin, cout = price_for(model, prices)
        cost = token_cost(cin, cout, task["input_tokens"], task["output_tokens"]) if cin is not None else 0.0
        if model["billing"] == "metered":
            metered += cost
        elif model["quota_pool"] == "ocgo":
            ocgo_usd += cost
            window = model["monthly_limit_usd"] * frac_5h
            if window:
                ocgo_share += cost / window
        elif model["quota_pool"] == "codex":
            codex_tokens += task["input_tokens"] + task["output_tokens"]
    return {
        "tool": tool,
        "scenario": scenario,
        "n": n,
        "exact": exact,
        "over": over,
        "under": under,
        "unrouted": unrouted,
        "exact_rate": rate(exact, n),
        "over_rate": rate(over, n),
        "under_rate": rate(under, n),
        "unrouted_rate": rate(unrouted, n),
        "wasted_first": wasted,
        "wasted_first_rate": rate(wasted, n),
        "failed_attempts": failed_sum,
        "review_n": review_n,
        "review_independent": review_ok,
        "review_independent_rate": rate(review_ok, review_n),
        "metered_usd": round(metered, 4),
        "ocgo_account_usd": round(ocgo_usd, 4),
        "ocgo_5h_share": round(ocgo_share, 4),
        "codex_tokens": codex_tokens,
        "codex_5h_share": round(codex_tokens / budget, 4) if budget else None,
    }


def attach_per_pass(block):
    """Mean of the per-scenario shares. Each scenario is one pass over the trace."""
    scenarios = list(block["scenarios"].values())
    n = len(scenarios) or 1
    for key in ("codex_5h_share", "ocgo_5h_share", "metered_usd", "ocgo_account_usd"):
        total = sum(s[key] or 0 for s in scenarios)
        block["overall"][key + "_per_pass"] = round(total / n, 4)
        for s in scenarios:
            s[key + "_per_pass"] = s[key]


def write_csv(path, blocks):
    fields = [
        "tool", "scenario", "n", "exact", "over", "under", "unrouted",
        "exact_rate", "over_rate", "under_rate", "unrouted_rate",
        "wasted_first", "wasted_first_rate", "failed_attempts",
        "review_n", "review_independent", "review_independent_rate",
        "metered_usd", "ocgo_account_usd", "ocgo_5h_share", "ocgo_5h_share_per_pass",
        "codex_tokens", "codex_5h_share", "codex_5h_share_per_pass",
        "metered_usd_per_pass", "ocgo_account_usd_per_pass",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for block in blocks:
            w.writerow({k: block["overall"][k] for k in fields})
            for scenario in block["scenarios"].values():
                w.writerow({k: scenario[k] for k in fields})


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


DISPLAY = {
    "openswarm": "openswarm",
    "omo": "OmO",
    "baseline_codex": "Codex default",
    "baseline_strongest": "strongest",
    "baseline_cheapest": "cheapest",
}
SERIES_COLORS = ["#1f4e79", "#c47b2b", "#3d6b58", "#6b4c7a", "#3f6f8f"]


def display_name(tool):
    return DISPLAY.get(tool, tool)


def text_width(text, size):
    # Conservative average for a sans-serif glyph, so near-misses still count.
    return len(str(text)) * size * 0.62


def text_bbox(x, y, text, size, anchor):
    width = text_width(text, size)
    if anchor == "middle":
        left = x - width / 2
    elif anchor == "end":
        left = x - width
    else:
        left = x
    return (left, y - size * 0.80, left + width, y + size * 0.25)


def boxes_overlap(a, b, pad=2):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def write_chart(path, blocks):
    """Two panels. Panel A is three objective percentages. Panel B is Codex 5h share per pass."""
    colors = SERIES_COLORS
    width, height = 1100, 760
    left, right = 86, 36
    plot_w = width - left - right
    texts = []
    rects = []
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]

    def add_text(x, y, text, size, anchor="start", fill="#222", weight="normal"):
        texts.append((text_bbox(x, y, text, size, anchor), str(text)))
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}">{esc(text)}</text>'
        )

    def add_rect(x, y, w, h, fill):
        rects.append((x, y, x + w, y + h))
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"/>')

    def add_line(x1, y1, x2, y2, stroke, extra=""):
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" {extra}/>'
        )

    add_text(left, 32, "Routing benchmark", 18, weight="600", fill="#1a1a1a")
    add_text(
        left, 54,
        "Wasted first attempts, unrouted decisions, and review independence. Percents of decisions.",
        12, fill="#444",
    )
    add_text(
        left, 72,
        "Lower panel: share of the nominal Codex 5h budget used by one pass of the 80-task trace.",
        12, fill="#444",
    )

    # Panel A
    a_top, a_bottom = 108, 348
    a_h = a_bottom - a_top
    metrics = [
        ("wasted_first_rate", "Wasted first attempts"),
        ("unrouted_rate", "Unrouted"),
        ("review_independent_rate", "Review independence"),
    ]
    n_series = len(blocks)
    group_w = plot_w / len(metrics)
    inner_pad = 16
    bar_w = (group_w - inner_pad * 2) / n_series
    for i in range(5):
        y = a_bottom - a_h * (i / 4)
        add_line(left, y, width - right, y, "#e6e6e6")
        add_text(left - 10, y + 4, f"{i * 25}%", 11, anchor="end", fill="#333")
    add_line(left, a_bottom, width - right, a_bottom, "#222")
    for g, (key, label) in enumerate(metrics):
        gx = left + g * group_w
        for s, block in enumerate(blocks):
            value = block["overall"].get(key) or 0
            bh = a_h * min(value, 1)
            x = gx + inner_pad + s * bar_w
            y = a_bottom - bh
            draw_w = max(bar_w - 6, 4)
            add_rect(x, y, draw_w, bh, colors[s % len(colors)])
            add_text(x + draw_w / 2, y - 8, f"{value * 100:.1f}", 11, anchor="middle")
        add_text(gx + group_w / 2, a_bottom + 24, label, 13, anchor="middle")

    legend_y = 396
    legend_step = 196
    for s, block in enumerate(blocks):
        x = left + s * legend_step
        add_rect(x, legend_y, 14, 14, colors[s % len(colors)])
        add_text(x + 20, legend_y + 12, display_name(block["tool"]), 13)

    # Panel B — its own axis, because a window-share is not a percent.
    add_text(left, 448, "Codex nominal 5h share per trace pass", 14, weight="600")
    add_text(left, 468, "1.0 is the whole nominal 5h window for one 80-task pass. Computed, not a quota.", 12, fill="#444")
    shares = [block["overall"].get("codex_5h_share_per_pass") or 0 for block in blocks]
    axis_max = 2.0
    while axis_max < max(shares + [0]) * 1.25:
        axis_max += 0.5
    b_top, b_bottom = 500, 680
    b_h = b_bottom - b_top
    ticks = int(axis_max / 0.5) + 1
    for i in range(ticks):
        value = i * 0.5
        y = b_bottom - b_h * (value / axis_max)
        add_line(left, y, width - right, y, "#e6e6e6")
        label = f"{value:.1f}"
        add_text(left - 10, y + 4, label, 11, anchor="end", fill="#333")
    add_line(left, b_bottom, width - right, b_bottom, "#222")
    slot = plot_w / n_series
    draw_w = min(64, slot - 36)
    for s, block in enumerate(blocks):
        value = shares[s]
        bh = b_h * (value / axis_max) if axis_max else 0
        x = left + s * slot + (slot - draw_w) / 2
        y = b_bottom - bh
        add_rect(x, y, draw_w, bh, colors[s % len(colors)])
        add_text(x + draw_w / 2, y - 8, f"{value:.2f}", 12, anchor="middle")
        add_text(x + draw_w / 2, b_bottom + 22, display_name(block["tool"]), 13, anchor="middle")

    problems = []
    for box, label in texts:
        if box[0] < 4 or box[1] < 2 or box[2] > width - 4 or box[3] > height - 2:
            problems.append(f"text {label!r} falls outside the canvas {box}")
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if boxes_overlap(texts[i][0], texts[j][0]):
                problems.append(f"text {texts[i][1]!r} overlaps {texts[j][1]!r}")
    for box, label in texts:
        for rect in rects:
            if boxes_overlap(box, rect, pad=1):
                # A value sitting in the gap above its own short bar is fine.
                # Overlap with a colored bar means the glyph runs into the fill.
                problems.append(f"text {label!r} overlaps a bar")
                break
    parts.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(parts) + "\n")
    if problems:
        # Keep the file so the geometry can be inspected, then fail the run.
        uniq = []
        for item in problems:
            if item not in uniq:
                uniq.append(item)
        raise SystemExit("chart labels overlap:\n" + "\n".join(uniq[:30]))


def fmt_pct(v):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%"


def scenario_table(blocks, cell):
    lines = []
    header = "| Scenario | " + " | ".join(display_name(b["tool"]) for b in blocks) + " |"
    lines.append(header)
    lines.append("| --- | " + " | ".join("---:" for _ in blocks) + " |")
    for sid in blocks[0]["scenarios"]:
        cells = " | ".join(cell(b["scenarios"][sid]) for b in blocks)
        lines.append(f"| {sid} | {cells} |")
    return lines


def unrouted_reason(row, chain):
    text = "\n".join(row.get("reasons") or [])
    if "requiresAnyModel" in text:
        reason = (
            "no model on this category's own chain is served by the connected "
            "providers (Codex, OpenCode Go, DeepSeek)"
        )
        if chain:
            reason += ". Chain providers: " + ", ".join(chain.get("providers") or [])
            reason += ". Chain models: " + ", ".join(chain.get("models") or [])
        return reason
    if "naive baseline" in text:
        return "the baseline's single model is on an exhausted quota and the baseline has no fallback"
    if "no reachable model" in text:
        return "every reachable rung in the category chain hit a usage-limit or insufficient-balance error"
    return "unrouted"


def unrouted_breakdown(series, trace, coverage):
    by_task = {t["id"]: t for t in trace}
    chains = ((coverage or {}).get("omo") or {}).get("category_chains") or {}
    order = {name: i for i, name in enumerate(DISPLAY)}
    buckets = {}
    for tool, rows in series:
        for row in rows:
            if row.get("model"):
                continue
            category = by_task[row["task_id"]]["omo_category"]
            reason = unrouted_reason(row, chains.get(category))
            key = (tool, category, reason)
            buckets[key] = buckets.get(key, 0) + 1
    rows = []
    for (tool, category, reason), n in sorted(
        buckets.items(),
        key=lambda item: (order.get(item[0][0], 99), item[0][1], -item[1], item[0][2]),
    ):
        rows.append({"tool": tool, "omo_category": category, "n": n, "reason": reason})
    return rows


def coverage_lines(coverage):
    lines = ["## Coverage, counted from the pinned sources", ""]
    if not coverage:
        lines.append("Coverage was not measured for this run.")
        lines.append("")
        return lines
    sw = coverage.get("openswarm") or {}
    om = coverage.get("omo") or {}
    lines.append(
        "These counts are read from the source trees at the commits in the environment section. "
        "They are not a quality score. OmO's chains name many more providers and model ids than this bench connects."
    )
    lines.append("")
    lines.append("| Router | Categories | Agents | Backends | Distinct providers in fallback chains | Distinct model ids |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    backends = ", ".join(sw.get("backend_names") or [])
    lines.append(
        f"| openswarm | — | — | {sw.get('backends', 'n/a')} ({backends}) | — | {sw.get('model_ids_in_policy', 'n/a')} in policy.json |"
    )
    if om:
        lines.append(
            f"| OmO model-core | {om.get('categories')} | {om.get('agents')} | — | {om.get('distinct_providers')} | {om.get('distinct_model_ids')} |"
        )
    else:
        lines.append("| OmO model-core | not measured | | | | |")
    lines.append("")
    lines.append(
        "openswarm's backends are `routing.default_order` in `policy.json`. "
        "Its model ids are the OpenCode Go monthly-cap keys plus `deepseek.tiers` values. "
        "Codex slugs are not listed in `policy.json`, so they are not in that count."
    )
    lines.append("")
    if om:
        commit = om.get("commit") or ""
        lines.append(
            f"OmO's providers and model ids are the union of the category fallback chains "
            f"({om.get('categories')} categories) and the agent fallback chains ({om.get('agents')} agents) "
            f"in `<omo-dir>/packages/model-core` at `{commit}`."
        )
    lines.append("")
    return lines


def write_summary(path, blocks, env, latency_notes, breakdown, coverage):
    lines = []
    lines.append("# Routing benchmark results")
    lines.append("")
    lines.append("All dollar amounts and window shares are computed from `bench/prices.json` times the trace's estimated tokens. They are not measured invoices.")
    lines.append("")
    lines.append("A wasted first attempt is a decision whose first try targeted a quota the tool only learns is exhausted by a usage-limit (or insufficient-balance) error. openswarm skips those backends before the call, so its wasted-attempt count stays at zero by construction.")
    lines.append("")
    lines.append("Subscription shares are reported two ways. The sum adds every scenario. The per-pass figure is the mean of the per-scenario shares, and each scenario is one pass over the 80-task trace. Read the per-pass number as the share of the nominal 5h budget one pass of the trace would use. OpenCode Go's 5h share can exceed 1 because it sums per-model fractions of small windows.")
    lines.append("")
    lines.append("The chart is `bench/results/chart.svg`. It plots the objective rates and the Codex per-pass share only.")
    lines.append("")
    lines.append("## Objective metrics")
    lines.append("")
    lines.append("| Tool | Wasted first | Unrouted | Review independence | Failed attempts | Metered USD | Go account USD | Go 5h share per pass | Go 5h share (sum) | Codex 5h share per pass | Codex 5h share (sum) | Codex tokens (sum) |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for block in blocks:
        o = block["overall"]
        lines.append(
            "| {tool} | {wasted} | {unrouted} | {review} | {failed} | {metered:.4f} | {go:.4f} | {go_pass:.4f} | {go_sum:.4f} | {cx_pass:.4f} | {cx_sum:.4f} | {tokens} |".format(
                tool=display_name(o["tool"]),
                wasted=fmt_pct(o["wasted_first_rate"]),
                unrouted=fmt_pct(o["unrouted_rate"]),
                review=fmt_pct(o["review_independent_rate"]),
                failed=o["failed_attempts"],
                metered=o["metered_usd"],
                go=o["ocgo_account_usd"],
                go_pass=o["ocgo_5h_share_per_pass"],
                go_sum=o["ocgo_5h_share"],
                cx_pass=o["codex_5h_share_per_pass"],
                cx_sum=o["codex_5h_share"],
                tokens=o["codex_tokens"],
            )
        )
    lines.append("")
    lines.append("## Unrouted decisions by OmO category")
    lines.append("")
    lines.append(
        "Unrouted counts below are a consequence of the provider set used for every tool in this bench: "
        "Codex (`chatgpt-subscription`), OpenCode Go, and DeepSeek. "
        "Providers that a chain names but this set does not connect — including Google and Anthropic — cannot be selected. "
        "That is a property of this comparison, not of OmO in general."
    )
    lines.append("")
    if not breakdown:
        lines.append("No tool left a decision unrouted.")
        lines.append("")
    else:
        lines.append("| Tool | OmO category | Decisions | Reason |")
        lines.append("| --- | --- | ---: | --- |")
        for row in breakdown:
            lines.append(
                f"| {display_name(row['tool'])} | {row['omo_category']} | {row['n']} | {row['reason']} |"
            )
        lines.append("")
    lines.extend(coverage_lines(coverage))
    lines.append("## Wasted first attempts by scenario")
    lines.append("")
    lines.extend(scenario_table(blocks, lambda s: fmt_pct(s["wasted_first_rate"])))
    lines.append("")
    lines.append("## Subscription share of one trace pass")
    lines.append("")
    lines.append("Each cell is that scenario alone: one pass over the 80-task trace, as a share of the nominal 5h budget.")
    lines.append("")
    lines.append("Codex nominal 5h share:")
    lines.append("")
    lines.extend(scenario_table(blocks, lambda s: f"{s['codex_5h_share_per_pass']:.4f}"))
    lines.append("")
    lines.append("OpenCode Go 5h share:")
    lines.append("")
    lines.extend(scenario_table(blocks, lambda s: f"{s['ocgo_5h_share_per_pass']:.4f}"))
    lines.append("")
    lines.append("## Review independence by scenario")
    lines.append("")
    lines.extend(scenario_table(blocks, lambda s: fmt_pct(s["review_independent_rate"])))
    lines.append("")
    lines.append("## Agreement with the difficulty→tier rubric")
    lines.append("")
    lines.append("This section is not a quality score and it is not the headline.")
    lines.append("")
    lines.append(
        "`min_tier` comes from openswarm's own difficulty map (difficulty 1–2 fast, 3–4 balanced, 5 deep, architecture deep). "
        "openswarm agrees with that map by construction whenever quota still allows the tier it would have picked on a fresh window. "
        "The rows below show how each router deviates from the rubric when a window is under pressure."
    )
    lines.append("")
    lines.append("An unrouted decision is counted in the under column here. The unrouted rate itself is in the objective table.")
    lines.append("")
    lines.append("| Scenario | " + " | ".join(display_name(b["tool"]) for b in blocks) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in blocks) + " |")
    for sid in blocks[0]["scenarios"]:
        cells = []
        for b in blocks:
            s = b["scenarios"][sid]
            cells.append(f"{fmt_pct(s['exact_rate'])} / {fmt_pct(s['over_rate'])} / {fmt_pct(s['under_rate'])}")
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Overall agreement (exact / over / under), same caveat:")
    lines.append("")
    for block in blocks:
        o = block["overall"]
        lines.append(
            f"- {display_name(o['tool'])}: {fmt_pct(o['exact_rate'])} / {fmt_pct(o['over_rate'])} / {fmt_pct(o['under_rate'])}"
        )
    lines.append("")
    lines.append("## Latency")
    lines.append("")
    lines.append("Latency is the only non-deterministic section. Decision files do not include it.")
    lines.append("")
    for note in latency_notes:
        lines.append(note)
    lines.append("")
    if env:
        lines.append("## Environment")
        lines.append("")
        for key in (
            "date", "os", "kernel", "cpu", "cores", "python", "bun",
            "openswarm_commit", "omo_dir", "omo_commit",
        ):
            if key in env and env[key] != "":
                value = env[key]
                if "<" in str(value):
                    lines.append(f"- {key}: `{value}`")
                else:
                    lines.append(f"- {key}: {value}")
        commands = env.get("commands") or []
        if commands:
            lines.append("- commands:")
            for cmd in commands:
                lines.append(f"  - `{cmd}`")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def latency_lines(path, label):
    if not path or not os.path.exists(path):
        return [f"- {label}: not measured"]
    data = load_json(path)
    lines = []
    inn = data.get("in_process") or {}
    if inn:
        lines.append(
            f"- {label} in-process: n={inn.get('n')} median={inn.get('median')} ms p95={inn.get('p95')} ms"
        )
    cli = data.get("cli_cold_start")
    if cli:
        lines.append(
            f"- {label} CLI cold start: n={cli.get('n')} median={cli.get('median')} ms p95={cli.get('p95')} ms"
        )
    elif label.startswith("OmO"):
        lines.append("- OmO: no standalone route CLI. " + (data.get("note") or ""))
    return lines


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--openswarm", required=True)
    ap.add_argument("--omo", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--env", default="")
    ap.add_argument("--latency-openswarm", default="")
    ap.add_argument("--latency-omo", default="")
    ap.add_argument("--coverage", default="")
    args = ap.parse_args()

    catalogue = load_json(os.path.join(BENCH, "catalogue.json"))
    scenarios = load_json(os.path.join(BENCH, "scenarios.json"))
    prices = load_json(os.path.join(BENCH, "prices.json"))
    trace = load_jsonl(os.path.join(BENCH, "trace.jsonl"))
    os.makedirs(args.out_dir, exist_ok=True)

    series = [("openswarm", load_jsonl(args.openswarm))]
    if args.omo and os.path.exists(args.omo):
        series.append(("omo", load_jsonl(args.omo)))
    base = baseline_rows(catalogue, scenarios, trace)
    with open(os.path.join(args.out_dir, "baselines.jsonl"), "w") as f:
        for row in base:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    for tool in ("baseline_codex", "baseline_strongest", "baseline_cheapest"):
        series.append((tool, [r for r in base if r["tool"] == tool]))

    blocks = [accumulate(name, rows, trace, scenarios, catalogue, prices) for name, rows in series]
    coverage = load_json(args.coverage) if args.coverage and os.path.exists(args.coverage) else {}
    breakdown = unrouted_breakdown(series, trace, coverage)
    scores = {
        "tools": blocks,
        "cost_label": prices["label"],
        "unrouted_breakdown": breakdown,
    }
    with open(os.path.join(args.out_dir, "scores.json"), "w") as f:
        json.dump(scores, f, indent=2, sort_keys=True)
        f.write("\n")
    write_csv(os.path.join(args.out_dir, "scores.csv"), blocks)
    write_chart(os.path.join(args.out_dir, "chart.svg"), blocks)
    env = load_json(args.env) if args.env and os.path.exists(args.env) else {}
    notes = latency_lines(args.latency_openswarm, "openswarm") + latency_lines(args.latency_omo, "OmO")
    write_summary(os.path.join(args.out_dir, "summary.md"), blocks, env, notes, breakdown, coverage)
    print("wrote scores to bench/results")


if __name__ == "__main__":
    main()
