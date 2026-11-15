---
name: council
description: >-
  Runs multi-model Q&A and planning inside Google Antigravity with no external
  servers. Council mode asks 2 to 5 models the same question, runs anonymous
  peer review, and synthesizes one final answer. Consensus mode asks 2 to 3
  blind models to revise one plan until all models approve the same version.
  Use this skill when the user asks for a council, second opinions, model peer
  review, or an agreed plan.
---

# Council Skill (`agy-council`)

This skill runs multi-model deliberation inside Google Antigravity. It needs no
external servers or API keys.

- **Primary engine (`swarm` + [`scripts/swarm_helper.py`](./scripts/swarm_helper.py))**:
  Assigns exact cross-vendor models to each seat. Defaults to **Gemini 4 Carbon**
  (`gemini-4-carbon-high`) and **Claude Opus 4.6** (`claude-opus-4-6-thinking`).
  Stores all prompts and outputs in local scratch files so models never read each
  other's work on the swarm message board.
- **Fallback engine (`run_workflow` + [`scripts/council_workflow.py`](./scripts/council_workflow.py))**:
  Runs automatically when the `swarm` tool is not available in the current session.

---

## 1. Check Models and Collect Inputs

1. Check if the `swarm` tool is available in your tool list.
2. Call `swarm(action="models", all=True)` to inspect available models and quotas.
   - Select **Seat 1**: `gemini-4-carbon-high` (or another `gemini-4-carbon*` model with quota above `0%`).
   - Select **Seat 2**: `claude-opus-4-6-thinking` (or `claude-sonnet-4-6` / `gpt-oss-120b-medium` if Opus has `0%` quota).
3. Ask one `ask_question` choice card for any missing settings:
   - **Mode**: `(Recommended) Council — Independent answers, peer review, and synthesis` or `Consensus Plan — Blind turn-by-turn plan agreement`
   - **Seats**: `(Recommended) Default: Gemini 4 Carbon + Claude Opus 4.6` or add extra models from `swarm(action="models")`
   - **Workspace access**: `(Recommended) Allow read-only file access` or `Prompt only (no file reads)`

---

## 2A. Run with `swarm` (Primary Engine)

Save the user's prompt to `<artifact_dir>/scratch/council/question.txt`. Save
optional extra context to `<artifact_dir>/scratch/council/context.txt`.

> **Important `swarm` rule**: Board tools (`post_message`, `read_board`, `swarm_wait`)
> appear one turn *after* the first `swarm(action="add")` call in a conversation.
> On that first turn, add the worker(s) and end your turn immediately. Do not add
> `narrator` or `monitor` workers.

### Council Mode (`council`)

1. Initialize Stage 1:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" init \
     --mode council \
     --out-dir "<artifact_dir>/scratch/council" \
     --seats "gemini-4-carbon-high,claude-opus-4-6-thinking" \
     --question-file "<artifact_dir>/scratch/council/question.txt" \
     --allow-tools
   ```
2. Set the swarm title with `swarm(action="title", title="Council Deliberation")`.
3. Add every worker listed in `swarm_add` with `swarm(action="add", ...)`. Wait until all files in `expected_outputs` exist.
4. Prepare Stage 2 (anonymized peer review):
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" prepare-reviews --run-dir "<run_dir>"
   ```
5. Send each string in `post_messages` via `post_message`. Call `swarm_wait` until all Stage 2 JSON files exist.
6. Prepare Stage 3 (Chairman synthesis):
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" prepare-chairman --run-dir "<run_dir>" --chairman auto
   ```
7. Send `post_messages[0]` via `post_message`. Call `swarm_wait` until `stage3_chair.md` exists.
8. Generate the final report and stop the workers:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" finalize-council --run-dir "<run_dir>"
   ```
   Call `swarm(action="kill", name=...)` for each worker in `workers_to_kill`.

### Consensus Mode (`consensus`)

1. Initialize Turn 1 (`Participant 1`):
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" init \
     --mode consensus \
     --out-dir "<artifact_dir>/scratch/council" \
     --seats "gemini-4-carbon-high,claude-opus-4-6-thinking" \
     --max-rounds 3 \
     --question-file "<artifact_dir>/scratch/council/question.txt" \
     --allow-tools
   ```
2. Set the swarm title with `swarm(action="title", title="Blind Consensus Plan")`.
3. Add `swarm_add[0]` and wait for `outputs/turn_1.json`.
4. Advance each turn until completion:
   ```bash
   python3 "<skill_dir>/scripts/swarm_helper.py" step-consensus --run-dir "<run_dir>"
   ```
   - When `status` is `"turn_ready"`, run any `swarm_add` entries and send any `post_messages`, then call `swarm_wait`.
   - When `status` is `"completed"`, stop all workers in `workers_to_kill` with `swarm(action="kill", name=...)`.

---

## 2B. Run with `run_workflow` (Fallback Engine)

If `swarm` is not available in your tool list, run [`scripts/council_workflow.py`](./scripts/council_workflow.py):

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
    "max_rounds": 3,
    "allow_tools": true,
    "out_dir": "<artifact_dir>/scratch/council"
  }
)
```

---

## 3. Publish the Artifact

1. Read `<run_dir>/report.md` and write it to `<artifact_dir>/council_<slug>.md`.
   - For `council` mode, set `UserFacing: true` and `RequestFeedback: false`.
   - For `consensus` mode, set `UserFacing: true` and `RequestFeedback: true` so the user can click **Proceed** to execute the plan.
2. Reply with a short status summary and link the artifact. Do not repeat the full report in chat.
