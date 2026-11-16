#!/usr/bin/env python3
"""State helper for running Council (Mode 1 & Mode 2) over Antigravity `swarm`.

Keeps all prompts, answers, anonymized peer reviews, and blind consensus drafts in
local files under `<out_dir>/<mode>-<timestamp>-<tag>/` so nothing leaks onto the
shared swarm message board. The swarm board is used strictly for barrier pings.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import json
import os
import random
import re
import secrets
import time
from typing import Any, Dict, List


DISPLAY_NAMES = {
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "gemini-4-carbon-high": "Gemini 4 Carbon (High)",
    "gemini-4-carbon-medium": "Gemini 4 Carbon (Medium)",
    "gemini-4-carbon-low": "Gemini 4 Carbon (Low)",
    "gemini-4-hydrogen-xhigh": "Gemini 4 Hydrogen (XHigh)",
    "gemini-4-hydrogen-high": "Gemini 4 Hydrogen (High)",
    "gemini-4-hydrogen-medium": "Gemini 4 Hydrogen (Medium)",
    "gemini-4-hydrogen-low": "Gemini 4 Hydrogen (Low)",
    "gemini-4-argon-high": "Gemini 4 Argon (High)",
    "gemini-4-argon-medium": "Gemini 4 Argon (Medium)",
    "gemini-4-argon-low": "Gemini 4 Argon (Low)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}

HELPER_PATH = os.path.abspath(__file__)


def pretty_model(model_id: str) -> str:
    return DISPLAY_NAMES.get(model_id, model_id)


def seat_label(idx: int, seats: List[str]) -> str:
    return f"Seat {idx + 1} — {pretty_model(seats[idx])}"


def letters(n: int) -> List[str]:
    return [chr(ord("A") + i) for i in range(n)]


def norm_plan(text: str) -> str:
    """Normalize line endings and trailing spaces while preserving indentation."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def load_state(run_dir: str) -> Dict[str, Any]:
    return json.loads(read_text(os.path.join(run_dir, "state.json")))


def save_state(run_dir: str, state: Dict[str, Any]) -> None:
    write_text(os.path.join(run_dir, "state.json"), json.dumps(state, indent=2))


def parse_json_lenient(raw: str) -> Any:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for open_ch, close_ch in (("[", "]"), ("{", "}")):
            start = text.find(open_ch)
            end = text.rfind(close_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise


def tools_instruction(allow_tools: bool) -> str:
    if allow_tools:
        return (
            "You may inspect files in the workspace (read-only) if helpful for accuracy, "
            "but NEVER edit workspace files (other than writing your designated output file) "
            "and NEVER call `read_board` or `swarm`."
        )
    return (
        "Answer strictly from the prompt and `<context>` below without reading workspace files. "
        "Only use `read_file` on your assigned prompt file, `write_file` on your assigned "
        "output file(s), `run_command` only for the `mark-done` helper if instructed, and "
        "`post_message` for barrier pings. NEVER call `read_board` or `swarm`."
    )


def barrier_brief(
    worker_name: str,
    run_dir: str,
    stage: str,
    seat_idx: int,
    prompt_path: str,
    output_path: str,
) -> str:
    return (
        f"You are an isolated deliberation participant (`{worker_name}`). "
        f"STRICT RULES: Never call `read_board` and never call `swarm`. Never post any "
        f"answer, critique, or plan text to the board. "
        f"1) Read `{prompt_path}`. "
        f"2) Write your complete output ONLY to `{output_path}`. "
        f'3) Run `python3 "{HELPER_PATH}" mark-done --run-dir "{run_dir}" --stage {stage} --seat {seat_idx}` '
        f"and if it returns `ping` (non-empty), call `post_message` with that exact `ping` text; otherwise end your turn silently."
    )


def direct_brief(worker_name: str, prompt_path: str, output_desc: str, ping_tag: str) -> str:
    return (
        f"You are an isolated deliberation participant (`{worker_name}`). "
        f"STRICT RULES: Never call `read_board` and never call `swarm`. Never post any "
        f"answer, critique, or plan text to the board. "
        f"1) Read `{prompt_path}`. "
        f"2) Write your output ONLY to {output_desc}. "
        f"3) Call `post_message` with exact text `@coordinator {ping_tag}` and end your turn."
    )


def barrier_wakeup(
    worker_name: str,
    run_dir: str,
    stage: str,
    seat_idx: int,
    prompt_path: str,
    output_path: str,
) -> str:
    return (
        f"@{worker_name} Read `{prompt_path}`, write your output ONLY to `{output_path}` "
        f"(never to the board, never call `read_board`), then run "
        f'`python3 "{HELPER_PATH}" mark-done --run-dir "{run_dir}" --stage {stage} --seat {seat_idx}`. '
        f"If `ping` is non-empty, call `post_message` with that exact text; otherwise end your turn silently."
    )


def direct_wakeup(worker_name: str, prompt_path: str, output_desc: str, ping_tag: str) -> str:
    return (
        f"@{worker_name} Read `{prompt_path}`, write your output ONLY to {output_desc} "
        f"(never to the board, never call `read_board`), then `post_message` exact text "
        f"`@coordinator {ping_tag}`."
    )


# ─────────────────────────────── INIT ───────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    mode = args.mode.lower()
    if mode not in ("council", "consensus"):
        raise SystemExit("mode must be 'council' or 'consensus'")

    seats = [s.strip() for s in args.seats.split(",") if s.strip()]
    if mode == "council" and not (2 <= len(seats) <= 5):
        raise SystemExit("council mode requires 2 to 5 seats")
    if mode == "consensus" and not (2 <= len(seats) <= 3):
        raise SystemExit("consensus mode requires 2 or 3 seats")

    question = read_text(args.question_file).strip() if args.question_file else (args.question or "").strip()
    if not question:
        raise SystemExit("question is required")
    context = read_text(args.context_file).strip() if args.context_file else (args.context or "").strip()

    tag = secrets.token_hex(2)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.abspath(os.path.join(args.out_dir, f"{mode}-{stamp}-{tag}"))
    os.makedirs(os.path.join(run_dir, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "outputs"), exist_ok=True)

    ctx_block = f"\n\n<context>\n{context}\n</context>" if context else ""
    t_note = tools_instruction(args.allow_tools)

    if mode == "council":
        # Default to fast mode when N == 2 unless --peer-review is explicitly requested.
        peer_review = bool(args.peer_review or len(seats) >= 3)
        workers = [f"seat{i}-{tag}" for i in range(len(seats))]
        add_calls = []
        expected_outputs = []
        for i, model_id in enumerate(seats):
            p_path = os.path.join(run_dir, "prompts", f"stage1_{i}.md")
            o_path = os.path.join(run_dir, "outputs", f"stage1_{i}.md")
            prompt = (
                "# Independent Question\n\n"
                "Answer the question below thoroughly, accurately, and insightfully in Markdown. "
                "If a diagram clarifies your answer, use a ```mermaid fenced block and always quote "
                'node labels containing parentheses or special characters (e.g. `A["Step (1)"]`).\n\n'
                f"{t_note}{ctx_block}\n\n"
                f"<question>\n{question}\n</question>\n\n"
                f"Write ONLY your Markdown answer to `{o_path}`."
            )
            write_text(p_path, prompt)
            expected_outputs.append(o_path)
            add_calls.append(
                {
                    "name": workers[i],
                    "model": model_id,
                    "brief": barrier_brief(workers[i], run_dir, "stage1", i, p_path, o_path),
                }
            )

        state = {
            "mode": "council",
            "peer_review": peer_review,
            "tag": tag,
            "run_dir": run_dir,
            "question": question,
            "context": context,
            "allow_tools": args.allow_tools,
            "seats": seats,
            "workers": workers,
            "stage": "stage1",
            "expected_stage_outputs": {"stage1": expected_outputs},
        }
        save_state(run_dir, state)
        print(
            json.dumps(
                {
                    "status": "stage1_ready",
                    "peer_review": peer_review,
                    "run_dir": run_dir,
                    "swarm_add": add_calls,
                    "expected_outputs": expected_outputs,
                },
                indent=2,
            )
        )
        return

    # Consensus mode: shuffle seats into blind Participant 1..K slots
    slot_order = list(range(len(seats)))
    random.shuffle(slot_order)
    workers = [f"p{p + 1}-{tag}" for p in range(len(seats))]
    p1_model = seats[slot_order[0]]

    p_path = os.path.join(run_dir, "prompts", "turn_1.md")
    meta_path = os.path.join(run_dir, "outputs", "turn_1.meta.json")
    plan_path = os.path.join(run_dir, "outputs", "turn_1.plan.md")
    prompt = (
        "# Planning Session — Turn 1\n\n"
        "You are working one-on-one to craft a rigorous, production-grade plan in Markdown.\n"
        f"{t_note}{ctx_block}\n\n"
        f"<request>\n{question}\n</request>\n\n"
        "Write TWO files:\n"
        f"1. `{plan_path}` — the complete standalone canonical Markdown plan.\n"
        f"2. `{meta_path}` — a JSON object:\n"
        '   `{"revision_notes": "Key design choices and rationale for this initial draft.", "agreed": false}`\n'
    )
    write_text(p_path, prompt)

    state = {
        "mode": "consensus",
        "tag": tag,
        "run_dir": run_dir,
        "question": question,
        "context": context,
        "allow_tools": args.allow_tools,
        "max_rounds": args.max_rounds,
        "seats": seats,
        "slot_order": slot_order,
        "workers": workers,
        "spawned_participants": [0],
        "turns_used": [0] * len(seats),
        "turn_number": 1,
        "active_participant": 0,
        "current_plan": "",
        "version": 0,
        "endorsed": [],
        "consolidation_pending": False,
        "history": [],
    }
    save_state(run_dir, state)
    print(
        json.dumps(
            {
                "status": "turn_ready",
                "run_dir": run_dir,
                "turn": 1,
                "swarm_add": [
                    {
                        "name": workers[0],
                        "model": p1_model,
                        "brief": direct_brief(
                            workers[0], p_path, f"`{plan_path}` and `{meta_path}`", "turn 1 done"
                        ),
                    }
                ],
                "post_messages": [],
                "expected_outputs": [meta_path, plan_path],
            },
            indent=2,
        )
    )


# ─────────────────────────── BARRIER HELPER ──────────────────────────


def cmd_mark_done(args: argparse.Namespace) -> None:
    lock_path = os.path.join(args.run_dir, f".{args.stage}.lock")
    done_path = os.path.join(args.run_dir, f".{args.stage}.pinged")
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(args.run_dir)
            expected = state.get("expected_stage_outputs", {}).get(args.stage, [])
            if not expected:
                n = len(state["seats"])
                ext = "md" if args.stage == "stage1" else "json"
                expected = [
                    os.path.join(args.run_dir, "outputs", f"{args.stage}_{i}.{ext}")
                    for i in range(n)
                ]
            all_present = all(os.path.exists(p) and os.path.getsize(p) > 0 for p in expected)
            if all_present and not os.path.exists(done_path):
                write_text(done_path, f"seat={args.seat}\n")
                ping = f"@coordinator {args.stage} all done"
                print(json.dumps({"all_done": True, "ping": ping}))
            else:
                print(json.dumps({"all_done": all_present, "ping": ""}))
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ─────────────────────────── MODE 1 STAGES ───────────────────────────


def cmd_prepare_reviews(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    seats = state["seats"]
    workers = state["workers"]
    n = len(seats)
    ctx_block = f"\n\n<context>\n{state['context']}\n</context>" if state["context"] else ""
    t_note = tools_instruction(state["allow_tools"])

    answers = []
    for i in range(n):
        ans_path = os.path.join(args.run_dir, "outputs", f"stage1_{i}.md")
        if not os.path.exists(ans_path):
            raise SystemExit(f"Missing Stage 1 output: {ans_path}")
        answers.append(read_text(ans_path).strip())

    review_mappings: Dict[str, Dict[str, int]] = {}
    post_messages = []
    expected_outputs = []

    for r in range(n):
        others = [j for j in range(n) if j != r]
        random.shuffle(others)
        mapping = dict(zip(letters(len(others)), others))
        review_mappings[str(r)] = mapping

        responses_block = "\n\n".join(
            f'<response label="{lab}">\n{answers[j]}\n</response>' for lab, j in mapping.items()
        )
        p_path = os.path.join(args.run_dir, "prompts", f"stage2_{r}.md")
        o_path = os.path.join(args.run_dir, "outputs", f"stage2_{r}.json")
        labels_str = ", ".join(f'"{lab}"' for lab in mapping)
        prompt = (
            "# Anonymized Peer Review\n\n"
            "Evaluate each anonymous response below strictly on technical merit.\n"
            "- **accuracy** (integer 1-10): factual correctness, absence of bugs or hallucinations.\n"
            "- **insight** (integer 1-10): depth, architectural judgment, non-obvious value.\n"
            "- **critique**: concise strengths, flaws, and omissions.\n\n"
            f"{t_note}{ctx_block}\n\n"
            f"<question>\n{state['question']}\n</question>\n\n"
            f"{responses_block}\n\n"
            f"Write ONLY a valid JSON array to `{o_path}` with one object per label ({labels_str}):\n"
            "[\n"
            '  {"label": "A", "accuracy": 8, "insight": 7, "critique": "..."}\n'
            "]\n"
        )
        write_text(p_path, prompt)
        expected_outputs.append(o_path)
        post_messages.append(barrier_wakeup(workers[r], args.run_dir, "stage2", r, p_path, o_path))

    state["answers"] = answers
    state["review_mappings"] = review_mappings
    state["stage"] = "stage2"
    state.setdefault("expected_stage_outputs", {})["stage2"] = expected_outputs
    save_state(args.run_dir, state)

    print(
        json.dumps(
            {
                "status": "stage2_ready",
                "post_messages": post_messages,
                "expected_outputs": expected_outputs,
            },
            indent=2,
        )
    )


def _clamp_score(val: Any, default: int = 5) -> int:
    try:
        num = int(round(float(val)))
    except (TypeError, ValueError):
        return default
    return max(1, min(10, num))


def cmd_prepare_chairman(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    seats = state["seats"]
    n = len(seats)

    answers = state.get("answers") or []
    if not answers:
        for i in range(n):
            ans_path = os.path.join(args.run_dir, "outputs", f"stage1_{i}.md")
            if not os.path.exists(ans_path):
                raise SystemExit(f"Missing Stage 1 output: {ans_path}")
            answers.append(read_text(ans_path).strip())
        state["answers"] = answers

    reviews: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(n)}
    leaderboard: List[Dict[str, Any]] = []

    if state.get("peer_review", True) and os.path.exists(
        os.path.join(args.run_dir, "outputs", "stage2_0.json")
    ):
        review_mappings = state["review_mappings"]
        reviewer_totals: Dict[int, List[float]] = {r: [] for r in range(n)}
        for r in range(n):
            o_path = os.path.join(args.run_dir, "outputs", f"stage2_{r}.json")
            if not os.path.exists(o_path):
                raise SystemExit(f"Missing Stage 2 output: {o_path}")
            items = parse_json_lenient(read_text(o_path))
            if isinstance(items, dict) and "reviews" in items:
                items = items["reviews"]
            if not isinstance(items, list):
                raise SystemExit(f"Stage 2 output for seat {r} is not a JSON list")
            mapping = review_mappings[str(r)]
            for item in items:
                if not isinstance(item, dict):
                    continue
                lab = str(item.get("label", "")).strip().upper()[:1]
                if lab not in mapping:
                    continue
                target = mapping[lab]
                acc = _clamp_score(item.get("accuracy"))
                ins = _clamp_score(item.get("insight"))
                crit = str(item.get("critique") or "").strip()
                reviewer_totals[r].append(float(acc + ins))
                reviews[target].append(
                    {
                        "reviewer": r,
                        "accuracy": acc,
                        "insight": ins,
                        "critique": crit,
                    }
                )

        # Reviewer mean-centering prevents harsh/generous grader bias when N >= 3.
        reviewer_means = {
            r: (sum(vals) / len(vals) if vals else 0.0) for r, vals in reviewer_totals.items()
        }
        for i in range(n):
            rs = reviews[i]
            acc = sum(x["accuracy"] for x in rs) / len(rs) if rs else 0.0
            ins = sum(x["insight"] for x in rs) / len(rs) if rs else 0.0
            centered = (
                sum((x["accuracy"] + x["insight"]) - reviewer_means[x["reviewer"]] for x in rs) / len(rs)
                if rs
                else 0.0
            )
            leaderboard.append(
                {
                    "seat": i,
                    "model": seats[i],
                    "display_name": pretty_model(seats[i]),
                    "accuracy": round(acc, 2),
                    "insight": round(ins, 2),
                    "total": round(acc + ins, 2),
                    "centered_delta": round(centered, 2),
                    "n": len(rs),
                }
            )
        leaderboard.sort(
            key=lambda b: (b["centered_delta"], b["total"], b["accuracy"]), reverse=True
        )

    order = list(range(n))
    random.shuffle(order)
    anon = dict(zip(letters(n), order))
    sections = []
    for lab, j in anon.items():
        crit_lines = "\n".join(
            f"- Accuracy {x['accuracy']}/10, Insight {x['insight']}/10: {x['critique']}"
            for x in reviews[j]
        )
        block = f'<response label="{lab}" seat="{seat_label(j, seats)}">\n{answers[j]}\n</response>'
        if crit_lines:
            block += f'\n<peer_reviews label="{lab}">\n{crit_lines}\n</peer_reviews>'
        sections.append(block)

    ctx_block = f"\n\n<context>\n{state['context']}\n</context>" if state["context"] else ""
    bundle_path = os.path.join(args.run_dir, "prompts", "stage3_bundle.md")
    o_path = os.path.join(args.run_dir, "outputs", "stage3_chair.md")
    bundle = (
        "# Coordinator Synthesis Bundle\n\n"
        "Synthesize the single definitive, authoritative final answer to the question below "
        "using the independent responses (and peer reviews, if present).\n"
        "- Keep verified technical details and strongest insights from each seat.\n"
        "- Correct flaws or omissions flagged during review.\n"
        "- Resolve disagreements explicitly.\n"
        "- Write ONLY the clean final Markdown answer (no panel meta-commentary) to "
        f"`{o_path}`.\n\n"
        f"{ctx_block}\n\n"
        f"<question>\n{state['question']}\n</question>\n\n"
        + "\n\n".join(sections)
    )
    write_text(bundle_path, bundle)

    state["reviews"] = {str(k): v for k, v in reviews.items()}
    state["leaderboard"] = leaderboard
    state["stage"] = "stage3"
    save_state(args.run_dir, state)

    print(
        json.dumps(
            {
                "status": "coordinator_synthesis_ready",
                "bundle_path": bundle_path,
                "output_path": o_path,
                "leaderboard": leaderboard,
                "workers_to_kill": state["workers"],
            },
            indent=2,
        )
    )


def cmd_finalize_council(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    seats = state["seats"]
    answers = state["answers"]
    reviews = {int(k): v for k, v in state.get("reviews", {}).items()}
    board = state.get("leaderboard", [])

    chair_path = os.path.join(args.run_dir, "outputs", "stage3_chair.md")
    if not os.path.exists(chair_path):
        raise SystemExit(f"Missing synthesis output: {chair_path}")
    final_answer = read_text(chair_path).strip()

    mode_note = (
        "Cross-Vendor Peer-Reviewed Council (Coordinator Synthesis)"
        if board
        else "Fast Cross-Vendor Council (Parallel Answers + Coordinator Synthesis)"
    )

    md = [
        f"# Council Deliberation — {state['question'][:80]}",
        "",
        f"> [!IMPORTANT]\n> **Mode**: {mode_note}",
        "",
        final_answer,
        "",
        "---",
        "",
    ]

    if board:
        md += [
            "## Peer Review Leaderboard",
            "",
            "| Rank | Seat | Accuracy (1–10) | Insight (1–10) | Combined (/20) | Calibrated Delta | Reviews |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
        ]
        for rank, b in enumerate(board, 1):
            delta = f"{b.get('centered_delta', 0.0):+.2f}"
            md.append(
                f"| {rank} | {seat_label(b['seat'], seats)} | {b['accuracy']:.1f} | "
                f"{b['insight']:.1f} | **{b['total']:.1f}** | {delta} | {b['n']} |"
            )
        md.append("")

    md += ["## Individual Responses", "", "````carousel"]
    for i in range(len(seats)):
        if i > 0:
            md.append("<!-- slide -->")
        md.append(f"### {seat_label(i, seats)}\n\n{answers[i]}")
        if reviews.get(i):
            md.append("\n#### Peer Critiques Received\n")
            for r in reviews[i]:
                md.append(
                    f"- **{seat_label(r['reviewer'], seats)}** *(Accuracy {r['accuracy']}/10, "
                    f"Insight {r['insight']}/10)*: {r['critique']}"
                )
    md.append("````")

    report_path = write_text(os.path.join(args.run_dir, "report.md"), "\n".join(md) + "\n")
    result = {
        "mode": "council",
        "peer_review": bool(board),
        "question": state["question"],
        "seats": seats,
        "leaderboard": board,
        "answers": answers,
        "reviews": state.get("reviews", {}),
        "final": final_answer,
    }
    write_text(os.path.join(args.run_dir, "result.json"), json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "status": "completed",
                "report": report_path,
                "workers_to_kill": state["workers"],
            },
            indent=2,
        )
    )


# ─────────────────────────── MODE 2 STEPPER ───────────────────────────


def render_consensus_report(state: Dict[str, Any], consensus: bool) -> str:
    seats = state["seats"]
    slot_order = state["slot_order"]
    history = state["history"]
    plan = state["current_plan"]
    version = state["version"]

    badge = (
        f"> [!TIP]\n> **Unanimous Blind Consensus** reached on **v{version}** after {len(history)} turns."
        if consensus
        else f"> [!WARNING]\n> **Max Rounds Reached** — Consolidated by `Participant 1` into **v{version}** after {len(history)} turns."
    )
    md = [
        f"# Consensus Plan — {state['question'][:80]}",
        "",
        badge,
        "",
        plan,
        "",
        "---",
        "",
        "<details><summary><strong>Reveal Blind Participant Mapping (Spoiler)</strong></summary>",
        "",
    ]
    for p_idx, seat_idx in enumerate(slot_order):
        md.append(f"- **Participant {p_idx + 1}**: {pretty_model(seats[seat_idx])} (`{seats[seat_idx]}`)")
    md += ["", "</details>", "", "## Deliberation History", ""]

    prev_plan = ""
    for item in history:
        tag = (
            "Consolidation"
            if item.get("consolidation")
            else ("Endorsed Unchanged" if item["agreed"] else "Revised Plan")
        )
        md.append(
            f"### Turn {item['turn']} · Participant {item['participant']} → v{item['version']} ({tag})\n\n"
            f"{item['notes']}\n"
        )
        if prev_plan and item["plan"] != prev_plan:
            diff_lines = "\n".join(
                difflib.unified_diff(
                    prev_plan.splitlines(),
                    item["plan"].splitlines(),
                    fromfile="previous",
                    tofile=f"v{item['version']}",
                    lineterm="",
                )
            )
            md.append(f"<details><summary>View Plan Diff</summary>\n\n```diff\n{diff_lines}\n```\n\n</details>\n")
        prev_plan = item["plan"]
    return "\n".join(md) + "\n"


def cmd_step_consensus(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    seats = state["seats"]
    slot_order = state["slot_order"]
    workers = state["workers"]
    k = len(seats)
    turn = state["turn_number"]
    p = state["active_participant"]

    meta_path = os.path.join(args.run_dir, "outputs", f"turn_{turn}.meta.json")
    plan_path = os.path.join(args.run_dir, "outputs", f"turn_{turn}.plan.md")
    if not os.path.exists(meta_path):
        raise SystemExit(f"Missing Turn {turn} metadata: {meta_path}")

    parsed = parse_json_lenient(read_text(meta_path))
    notes = str(parsed.get("revision_notes") or "").strip()
    claimed_agreed = bool(parsed.get("agreed", False))

    if os.path.exists(plan_path) and os.path.getsize(plan_path) > 0:
        new_plan = read_text(plan_path).strip()
    elif turn > 1 and claimed_agreed and state["current_plan"]:
        new_plan = state["current_plan"]
    else:
        raise SystemExit(f"Missing Turn {turn} plan file: {plan_path}")

    if state["consolidation_pending"]:
        state["version"] += 1
        state["current_plan"] = new_plan
        state["history"].append(
            {
                "turn": turn,
                "participant": p + 1,
                "notes": notes,
                "plan": new_plan,
                "agreed": True,
                "version": state["version"],
                "consolidation": True,
            }
        )
        save_state(args.run_dir, state)
        report_md = render_consensus_report(state, consensus=False)
        report_path = write_text(os.path.join(args.run_dir, "report.md"), report_md)
        write_text(
            os.path.join(args.run_dir, "result.json"),
            json.dumps({**state, "consensus": False}, indent=2),
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "consensus": False,
                    "report": report_path,
                    "workers_to_kill": [workers[i] for i in state["spawned_participants"]],
                },
                indent=2,
            )
        )
        return

    state["turns_used"][p] += 1
    if turn == 1:
        state["current_plan"] = new_plan
        state["version"] = 1
        state["endorsed"] = [p]
        effective_agreed = False
    else:
        changed = norm_plan(new_plan) != norm_plan(state["current_plan"])
        if claimed_agreed and not changed:
            if p not in state["endorsed"]:
                state["endorsed"].append(p)
            effective_agreed = True
        else:
            state["current_plan"] = new_plan
            state["version"] += 1
            state["endorsed"] = [p]
            effective_agreed = False

    state["history"].append(
        {
            "turn": turn,
            "participant": p + 1,
            "notes": notes,
            "plan": state["current_plan"],
            "agreed": effective_agreed,
            "version": state["version"],
        }
    )

    if len(state["endorsed"]) == k:
        save_state(args.run_dir, state)
        report_md = render_consensus_report(state, consensus=True)
        report_path = write_text(os.path.join(args.run_dir, "report.md"), report_md)
        write_text(
            os.path.join(args.run_dir, "result.json"),
            json.dumps({**state, "consensus": True}, indent=2),
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "consensus": True,
                    "report": report_path,
                    "workers_to_kill": [workers[i] for i in state["spawned_participants"]],
                },
                indent=2,
            )
        )
        return

    next_p = None
    for offset in range(1, k + 1):
        cand = (p + offset) % k
        if state["turns_used"][cand] < state["max_rounds"]:
            next_p = cand
            break

    ctx_block = f"\n\n<context>\n{state['context']}\n</context>" if state["context"] else ""
    t_note = tools_instruction(state["allow_tools"])
    next_turn = turn + 1
    p_path = os.path.join(args.run_dir, "prompts", f"turn_{next_turn}.md")
    next_meta = os.path.join(args.run_dir, "outputs", f"turn_{next_turn}.meta.json")
    next_plan = os.path.join(args.run_dir, "outputs", f"turn_{next_turn}.plan.md")

    if next_p is None:
        state["consolidation_pending"] = True
        state["turn_number"] = next_turn
        state["active_participant"] = 0
        prompt = (
            f"# Final Plan Consolidation — Turn {next_turn}\n\n"
            f"<request>\n{state['question']}\n</request>\n\n"
            "We have reached the maximum revision budget. Produce the final consolidated "
            "standalone Markdown plan from the latest draft below: preserve the strongest "
            "decisions and resolve any remaining tensions explicitly in a concise "
            "'Key Trade-offs & Decisions' section.\n\n"
            f"<draft version=\"{state['version']}\">\n{state['current_plan']}\n</draft>\n\n"
            "Write TWO files:\n"
            f"1. `{next_plan}` — the complete final standalone Markdown plan.\n"
            f"2. `{next_meta}` — `{{\"revision_notes\": \"Summary of final consolidation\", \"agreed\": true}}`\n"
        )
        write_text(p_path, prompt)
        save_state(args.run_dir, state)
        print(
            json.dumps(
                {
                    "status": "turn_ready",
                    "turn": next_turn,
                    "consolidation": True,
                    "swarm_add": [],
                    "post_messages": [
                        direct_wakeup(
                            workers[0], p_path, f"`{next_plan}` and `{next_meta}`", f"turn {next_turn} done"
                        )
                    ],
                    "expected_outputs": [next_meta, next_plan],
                },
                indent=2,
            )
        )
        return

    first_time = next_p not in state["spawned_participants"]
    prompt = (
        f"# Plan Review — Turn {next_turn} (Draft v{state['version']})\n\n"
        "You are working one-on-one to refine a rigorous, production-grade plan.\n"
        f"{t_note}{ctx_block}\n\n"
        f"<request>\n{state['question']}\n</request>\n\n"
        "Evaluate the draft below on technical merit, completeness, edge cases, sequencing, and clarity.\n"
        f"- **If you endorse this exact draft as-is**: write ONLY `{next_meta}` with "
        "`{\"revision_notes\": \"Why you endorse as-is\", \"agreed\": true}` and do NOT write a plan file.\n"
        f"- **If you change anything**: write the COMPLETE updated Markdown plan to `{next_plan}`, "
        f"and write `{next_meta}` with `{{\"revision_notes\": \"What you changed and why\", \"agreed\": false}}`.\n\n"
        f"<draft version=\"{state['version']}\">\n{state['current_plan']}\n</draft>\n"
    )
    write_text(p_path, prompt)

    swarm_add = []
    post_messages = []
    out_desc = f"`{next_meta}` (and `{next_plan}` only if revised)"
    if first_time:
        state["spawned_participants"].append(next_p)
        swarm_add.append(
            {
                "name": workers[next_p],
                "model": seats[slot_order[next_p]],
                "brief": direct_brief(workers[next_p], p_path, out_desc, f"turn {next_turn} done"),
            }
        )
    else:
        post_messages.append(
            direct_wakeup(workers[next_p], p_path, out_desc, f"turn {next_turn} done")
        )

    state["turn_number"] = next_turn
    state["active_participant"] = next_p
    save_state(args.run_dir, state)
    print(
        json.dumps(
            {
                "status": "turn_ready",
                "turn": next_turn,
                "swarm_add": swarm_add,
                "post_messages": post_messages,
                "expected_outputs": [next_meta],
            },
            indent=2,
        )
    )


# ───────────────────────── RECOVERY & SWAP ───────────────────────────


def cmd_resume(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    mode = state["mode"]
    if mode == "council":
        stage = state["stage"]
        n = len(state["seats"])
        ext = "md" if stage == "stage1" else "json"
        if stage in ("stage1", "stage2"):
            missing = []
            wakeups = []
            for i in range(n):
                o_path = os.path.join(args.run_dir, "outputs", f"{stage}_{i}.{ext}")
                p_path = os.path.join(args.run_dir, "prompts", f"{stage}_{i}.md")
                if not os.path.exists(o_path) or os.path.getsize(o_path) == 0:
                    missing.append(o_path)
                    wakeups.append(
                        barrier_wakeup(state["workers"][i], args.run_dir, stage, i, p_path, o_path)
                    )
            print(
                json.dumps(
                    {
                        "mode": "council",
                        "stage": stage,
                        "missing_outputs": missing,
                        "post_messages": wakeups,
                    },
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                {
                    "mode": "council",
                    "stage": stage,
                    "bundle_path": os.path.join(args.run_dir, "prompts", "stage3_bundle.md"),
                    "output_path": os.path.join(args.run_dir, "outputs", "stage3_chair.md"),
                },
                indent=2,
            )
        )
        return

    turn = state["turn_number"]
    p = state["active_participant"]
    p_path = os.path.join(args.run_dir, "prompts", f"turn_{turn}.md")
    meta_path = os.path.join(args.run_dir, "outputs", f"turn_{turn}.meta.json")
    plan_path = os.path.join(args.run_dir, "outputs", f"turn_{turn}.plan.md")
    print(
        json.dumps(
            {
                "mode": "consensus",
                "turn": turn,
                "active_participant": p + 1,
                "post_messages": [
                    direct_wakeup(
                        state["workers"][p],
                        p_path,
                        f"`{meta_path}` (and `{plan_path}` if revised)",
                        f"turn {turn} done",
                    )
                ],
                "expected_outputs": [meta_path],
            },
            indent=2,
        )
    )


def cmd_swap_seat(args: argparse.Namespace) -> None:
    state = load_state(args.run_dir)
    idx = args.seat
    if not (0 <= idx < len(state["seats"])):
        raise SystemExit(f"Invalid seat index {idx}")
    old_model = state["seats"][idx]
    state["seats"][idx] = args.model
    new_name = f"seat{idx}-{state['tag']}-r" if state["mode"] == "council" else f"p{idx + 1}-{state['tag']}-r"
    old_name = state["workers"][idx]
    state["workers"][idx] = new_name
    save_state(args.run_dir, state)
    print(
        json.dumps(
            {
                "status": "swapped",
                "seat": idx,
                "old_model": old_model,
                "new_model": args.model,
                "kill_worker": old_name,
                "new_worker": new_name,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Council Swarm State Helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--mode", required=True, choices=["council", "consensus"])
    p_init.add_argument("--out-dir", required=True)
    p_init.add_argument(
        "--seats",
        default="gemini-4-carbon-high,claude-opus-4-6-thinking",
        help="Comma-separated swarm model IDs",
    )
    p_init.add_argument("--question", default="")
    p_init.add_argument("--question-file", default="")
    p_init.add_argument("--context", default="")
    p_init.add_argument("--context-file", default="")
    p_init.add_argument("--max-rounds", type=int, default=2)
    p_init.add_argument("--allow-tools", action="store_true", default=False)
    p_init.add_argument("--peer-review", action="store_true", default=False)
    p_init.set_defaults(func=cmd_init)

    p_mark = sub.add_parser("mark-done")
    p_mark.add_argument("--run-dir", required=True)
    p_mark.add_argument("--stage", required=True, choices=["stage1", "stage2"])
    p_mark.add_argument("--seat", required=True, type=int)
    p_mark.set_defaults(func=cmd_mark_done)

    p_rev = sub.add_parser("prepare-reviews")
    p_rev.add_argument("--run-dir", required=True)
    p_rev.set_defaults(func=cmd_prepare_reviews)

    p_chair = sub.add_parser("prepare-chairman")
    p_chair.add_argument("--run-dir", required=True)
    p_chair.set_defaults(func=cmd_prepare_chairman)

    p_fin = sub.add_parser("finalize-council")
    p_fin.add_argument("--run-dir", required=True)
    p_fin.set_defaults(func=cmd_finalize_council)

    p_step = sub.add_parser("step-consensus")
    p_step.add_argument("--run-dir", required=True)
    p_step.set_defaults(func=cmd_step_consensus)

    p_res = sub.add_parser("resume")
    p_res.add_argument("--run-dir", required=True)
    p_res.set_defaults(func=cmd_resume)

    p_swap = sub.add_parser("swap-seat")
    p_swap.add_argument("--run-dir", required=True)
    p_swap.add_argument("--seat", required=True, type=int)
    p_swap.add_argument("--model", required=True)
    p_swap.set_defaults(func=cmd_swap_seat)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
