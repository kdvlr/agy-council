---
name: council
description: >-
  Runs cross-vendor multi-model Q&A and planning inside Google Antigravity with
  no external servers. Council mode gathers independent answers (with optional
  peer review for 3+ seats) and synthesizes one final response. Consensus mode
  asks 2 to 3 blind models to revise one plan until all approve the same
  version. Use when the user asks for a council, second opinions, model peer
  review, or a high-confidence agreed plan.
---

# Council Skill (`agy-council`)

This skill runs multi-model deliberation inside Google Antigravity. It needs no
external servers or API keys.

- **Primary engine (`swarm` + [`scripts/swarm_helper.py`](./scripts/swarm_helper.py))**:
  Assigns exact cross-vendor models to each seat. Defaults to **Gemini 4 Carbon**
  (`gemini-4-carbon-high`) and **Claude Opus 4.6** (`claude-opus-4-6-thinking`).
  Stores all prompts, drafts, and reviews in local scratch files. Uses barrier
  pings so the coordinator wakes once per parallel stage.
- **Fallback engine (`run_workflow` + [`scripts/council_workflow.py`](./scripts/council_workflow.py))**:
  Runs only when `swarm` is unavailable in the current session.

---

## 1. Check Models, Scout Context, and Confirm Scope

1. Check if the `swarm` tool is in your tool list.
   - If `swarm` is missing, warn the user that `run_workflow` only runs
     same-vendor tiers (`inherit` + `pro`) and confirm before starting.
2. Call `swarm(action="models", all=True)` to inspect available models and quotas.
   - **Seat 1**: `gemini-4-carbon-high` (or another `gemini-4-carbon*` model above `0%` quota).
   - **Seat 2**: `claude-opus-4-6-thinking` (or `claude-sonnet-4-6` / `gpt-oss-120b-medium` if Opus is at `0%` quota).
3. **Skip `ask_question` if intent is clear.** Only ask one `ask_question` card
   when mode or seats are ambiguous. Infer `consensus` for migration, rollout,
   or architecture plans; infer `council` for trade-offs, audits, or reviews.
4. **Pre-Flight Context Scout (run once)**:
   If the question refers to files in the workspace, read the relevant files and
   save concise excerpts to `<artifact_dir>/scratch/council/context.txt`. Keep
   `--allow-tools` **off** by default so N seats do not re-read the whole repo.
   Pass `--allow-tools` only when the user asks seats to inspect files directly.
5. State the expected cost before starting:
   - **Fast Council (2 seats)**: 2 parallel worker turns + coordinator synthesis.
   - **Peer-Reviewed Council (3+ seats or `--peer-review`)**: `2N` worker turns + coordinator synthesis.
   - **Consensus Plan (2 seats, `max_rounds=2`)**: 2 to 5 sequential worker turns.

---

## 2A. Run with `swarm` (Primary Engine)

Save the user's request to `<artifact_dir>/scratch/council/question.txt`.

> **Important `swarm` rule**: Board tools (`post_message`, `read_board`, `swarm_wait`)
> appear one turn *after* the first `swarm(action="add")` call in a conversation.
> On that first turn, add the worker(s) and end your turn immediately. Do not add
> `narrator` or `monitor` workers.

### Council Mode (`council`)

1. Initialize Stage 1 (omit `--allow-tools` when `context.txt` covers the task;
   add `--peer-review` only if N >= 3 or requested):
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" init \
     --mode council \
     --out-dir "<artifact_dir>/scratch/council" \
     --seats "gemini-4-carbon-high,claude-opus-4-6-thinking" \
     --question-file "<artifact_dir>/scratch/council/question.txt" \
     --context-file "<artifact_dir>/scratch/council/context.txt"
   ```
2. Call `swarm(action="title", title="Council Deliberation")` and add each worker
   in `swarm_add`. Workers run `mark-done` and post `@coordinator stage1 all done`
   once all Stage 1 files exist.
3. **If `peer_review` is `true` (3+ seats or `--peer-review`)**:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" prepare-reviews --run-dir "<run_dir>"
   ```
   Send each string in `post_messages` via `post_message` and `swarm_wait` for
   `@coordinator stage2 all done`.
4. **Coordinator Synthesis**:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" prepare-chairman --run-dir "<run_dir>"
   ```
   Kill all workers in `workers_to_kill` via `swarm(action="kill", name=...)`.
   Read `bundle_path` (`prompts/stage3_bundle.md`) and write the clean,
   synthesized Markdown answer to `output_path` (`outputs/stage3_chair.md`).
5. Finalize the report:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" finalize-council --run-dir "<run_dir>"
   ```

### Consensus Mode (`consensus`)

1. Initialize Turn 1 (`Participant 1`, default `--max-rounds 2`):
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" init \
     --mode consensus \
     --out-dir "<artifact_dir>/scratch/council" \
     --seats "gemini-4-carbon-high,claude-opus-4-6-thinking" \
     --max-rounds 2 \
     --question-file "<artifact_dir>/scratch/council/question.txt" \
     --context-file "<artifact_dir>/scratch/council/context.txt"
   ```
2. Call `swarm(action="title", title="Blind Consensus Plan")`, add `swarm_add[0]`,
   and wait for `outputs/turn_1.meta.json` and `outputs/turn_1.plan.md`.
3. Advance each turn until completion:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" step-consensus --run-dir "<run_dir>"
   ```
   - When `status` is `"turn_ready"`, run any `swarm_add` entries and send any
     `post_messages`, then call `swarm_wait`.
   - When `status` is `"completed"`, stop all workers in `workers_to_kill` with
     `swarm(action="kill", name=...)`.

### Recovery (`resume` and `swap-seat`)

- If a run pauses mid-stage, run `swarm_helper.py resume --run-dir "<run_dir>"`
  to inspect missing outputs and get exact wakeup messages.
- If a model hits `0%` quota mid-run, run `swarm_helper.py swap-seat --run-dir "<run_dir>" --seat <idx> --model <new_model>`, kill the old worker, and add the replacement.

---

## 2B. Run with `run_workflow` (Fallback Engine)

If `swarm` is not in your tool list, run [`scripts/council_workflow.py`](./scripts/council_workflow.py):

```
run_workflow(
  ScriptPath="<skill_dir>/scripts/council_workflow.py",
  Description="Council: <short question>",
  Args={
    "mode": "council" | "consensus",
    "question": "<question>",
    "context": "<context>",
    "seats": ["inherit", "pro"],
    "chairman": "auto",
    "max_rounds": 2,
    "allow_tools": false,
    "out_dir": "<artifact_dir>/scratch/council"
  }
)
```

---

## 3. Publish the Artifact

1. Read `<run_dir>/report.md` and write it to `<artifact_dir>/council_<slug>.md`.
   - For `council` mode, set `UserFacing: true` and `RequestFeedback: false`.
   - For `consensus` mode, set `UserFacing: true` and `RequestFeedback: true` so the user can click **Proceed** to execute the plan.
2. Reply with a short summary and link the artifact. Do not repeat the full report in chat.
