import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "scripts", "swarm_helper.py")
WORKFLOW = os.path.join(ROOT, "scripts", "council_workflow.py")


class AgyCouncilTests(unittest.TestCase):
    def run_helper(self, *args: str) -> dict:
        out = subprocess.check_output([sys.executable, HELPER, *args], text=True)
        return json.loads(out)

    def test_fast_council_2_seats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = self.run_helper(
                "init",
                "--mode",
                "council",
                "--out-dir",
                tmp,
                "--question",
                "Compare SQLite WAL and PostgreSQL.",
            )
            self.assertFalse(r1["peer_review"])
            self.assertEqual(
                [x["model"] for x in r1["swarm_add"]],
                ["gemini-4-carbon-high", "claude-opus-4-6-thinking"],
            )
            run_dir = r1["run_dir"]
            # Barrier test: first mark-done does not ping, second pings once.
            with open(r1["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write("Answer from Carbon.")
            m0 = self.run_helper("mark-done", "--run-dir", run_dir, "--stage", "stage1", "--seat", "0")
            self.assertFalse(m0["all_done"])
            self.assertEqual(m0["ping"], "")

            with open(r1["expected_outputs"][1], "w", encoding="utf-8") as f:
                f.write("Answer from Opus.")
            m1 = self.run_helper("mark-done", "--run-dir", run_dir, "--stage", "stage1", "--seat", "1")
            self.assertTrue(m1["all_done"])
            self.assertEqual(m1["ping"], "@coordinator stage1 all done")

            # Duplicate mark-done does not double-ping.
            m1_dup = self.run_helper("mark-done", "--run-dir", run_dir, "--stage", "stage1", "--seat", "1")
            self.assertEqual(m1_dup["ping"], "")

            r3 = self.run_helper("prepare-chairman", "--run-dir", run_dir)
            self.assertEqual(r3["status"], "coordinator_synthesis_ready")
            self.assertEqual(r3["leaderboard"], [])
            with open(r3["output_path"], "w", encoding="utf-8") as f:
                f.write("Coordinator synthesized answer.")

            r4 = self.run_helper("finalize-council", "--run-dir", run_dir)
            self.assertEqual(r4["status"], "completed")
            self.assertTrue(os.path.exists(r4["report"]))

    def test_peer_reviewed_council_3_seats_mean_centered_and_defensive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = self.run_helper(
                "init",
                "--mode",
                "council",
                "--out-dir",
                tmp,
                "--seats",
                "gemini-4-carbon-high,claude-opus-4-6-thinking,gpt-oss-120b-medium",
                "--question",
                "Security audit of auth boundaries.",
            )
            self.assertTrue(r1["peer_review"])
            run_dir = r1["run_dir"]
            for idx, path in enumerate(r1["expected_outputs"]):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Answer {idx}")

            r2 = self.run_helper("prepare-reviews", "--run-dir", run_dir)
            # Include clamped out-of-range score, null insight, and markdown fence
            with open(r2["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write('```json\n[{"label":"A","accuracy":15,"insight":null,"critique":"Good"},{"label":"B","accuracy":7,"insight":7,"critique":"Ok"}]\n```')
            with open(r2["expected_outputs"][1], "w", encoding="utf-8") as f:
                f.write('[{"label":"A","accuracy":9,"insight":9,"critique":"Sharp"},{"label":"B","accuracy":8,"insight":8,"critique":"Solid"}]')
            with open(r2["expected_outputs"][2], "w", encoding="utf-8") as f:
                f.write('[{"label":"A","accuracy":8,"insight":9,"critique":"Nice"},{"label":"B","accuracy":9,"insight":9,"critique":"Great"}]')

            r3 = self.run_helper("prepare-chairman", "--run-dir", run_dir)
            self.assertEqual(len(r3["leaderboard"]), 3)
            self.assertIn("centered_delta", r3["leaderboard"][0])
            self.assertTrue(os.path.exists(r3["bundle_path"]))
            with open(r3["output_path"], "w", encoding="utf-8") as f:
                f.write("Synthesized 3-seat answer.")

            r4 = self.run_helper("finalize-council", "--run-dir", run_dir)
            self.assertEqual(r4["status"], "completed")

    def test_consensus_decoupled_approval_and_indentation_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q_file = os.path.join(tmp, "q.txt")
            c_file = os.path.join(tmp, "c.txt")
            with open(q_file, "w", encoding="utf-8") as f:
                f.write("Write a deployment plan.")
            with open(c_file, "w", encoding="utf-8") as f:
                f.write("Target host is Debian.")

            c1 = self.run_helper(
                "init",
                "--mode",
                "consensus",
                "--out-dir",
                tmp,
                "--question-file",
                q_file,
                "--context-file",
                c_file,
            )
            run_dir = c1["run_dir"]
            with open(os.path.join(run_dir, "outputs", "turn_1.plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan\n```python\ndef f():\n  return 1\n```")
            with open(os.path.join(run_dir, "outputs", "turn_1.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Initial v1", "agreed": False}, f)

            # Turn 2: Participant 2 fixes Python indentation (must count as revised, NOT equal!)
            c2 = self.run_helper("step-consensus", "--run-dir", run_dir)
            with open(os.path.join(run_dir, "prompts", "turn_2.md"), encoding="utf-8") as f:
                turn2_prompt = f.read()
            self.assertIn("<request>", turn2_prompt)
            self.assertIn("Target host is Debian.", turn2_prompt)
            with open(os.path.join(run_dir, "outputs", "turn_2.plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan\n```python\ndef f():\n    return 1\n```")
            with open(os.path.join(run_dir, "outputs", "turn_2.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Fix 4-space indent", "agreed": False}, f)

            # Turn 3: Participant 1 approves unchanged by writing ONLY meta.json (no plan.md!)
            c3 = self.run_helper("step-consensus", "--run-dir", run_dir)
            with open(os.path.join(run_dir, "prompts", "turn_3.md"), encoding="utf-8") as f:
                turn3_prompt = f.read()
            self.assertIn("<request>", turn3_prompt)
            with open(os.path.join(run_dir, "outputs", "turn_3.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Endorsed as-is", "agreed": True}, f)

            c4 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertEqual(c4["status"], "completed")
            self.assertTrue(c4["consensus"])

    def test_consensus_deadlock_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d1 = self.run_helper(
                "init",
                "--mode",
                "consensus",
                "--out-dir",
                tmp,
                "--question",
                "Deadlock test.",
                "--max-rounds",
                "1",
            )
            run_dir = d1["run_dir"]
            with open(os.path.join(run_dir, "outputs", "turn_1.plan.md"), "w", encoding="utf-8") as f:
                f.write("Plan v1")
            with open(os.path.join(run_dir, "outputs", "turn_1.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "v1", "agreed": False}, f)

            self.run_helper("step-consensus", "--run-dir", run_dir)
            with open(os.path.join(run_dir, "outputs", "turn_2.plan.md"), "w", encoding="utf-8") as f:
                f.write("Plan v2")
            with open(os.path.join(run_dir, "outputs", "turn_2.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "v2", "agreed": False}, f)

            d3 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertTrue(d3.get("consolidation"))
            with open(os.path.join(run_dir, "outputs", "turn_3.plan.md"), "w", encoding="utf-8") as f:
                f.write("Consolidated Plan")
            with open(os.path.join(run_dir, "outputs", "turn_3.meta.json"), "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Merged", "agreed": True}, f)

            d4 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertEqual(d4["status"], "completed")
            self.assertFalse(d4["consensus"])

    def test_resume_and_swap_seat_and_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = self.run_helper(
                "init",
                "--mode",
                "council",
                "--out-dir",
                tmp,
                "--seats",
                "gemini-4-carbon-high,claude-opus-4-6-thinking,gpt-oss-120b-medium",
                "--question",
                "Test recovery.",
            )
            run_dir = r1["run_dir"]
            with open(r1["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write("Seat 0 done.")

            res = self.run_helper("resume", "--run-dir", run_dir)
            self.assertEqual(len(res["missing_outputs"]), 2)

            sw = self.run_helper(
                "swap-seat", "--run-dir", run_dir, "--seat", "1", "--model", "claude-sonnet-4-6"
            )
            self.assertEqual(sw["new_model"], "claude-sonnet-4-6")

            # Malformed prose in Stage 2 raises non-zero exit rather than silent pass
            with open(r1["expected_outputs"][1], "w", encoding="utf-8") as f:
                f.write("Seat 1 done.")
            with open(r1["expected_outputs"][2], "w", encoding="utf-8") as f:
                f.write("Seat 2 done.")
            r2 = self.run_helper("prepare-reviews", "--run-dir", run_dir)
            with open(r2["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write("Not JSON prose")
            with open(r2["expected_outputs"][1], "w", encoding="utf-8") as f:
                f.write("[]")
            with open(r2["expected_outputs"][2], "w", encoding="utf-8") as f:
                f.write("[]")
            proc = subprocess.run(
                [sys.executable, HELPER, "prepare-chairman", "--run-dir", run_dir],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)

    def test_fallback_workflow_executes_with_mocked_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, open(WORKFLOW, "r", encoding="utf-8") as f:
            source = f.read()

        async def _run() -> None:
            def fake_phase(_name: str) -> None:
                return None

            def fake_log(_msg: str) -> None:
                return None

            async def fake_parallel(items, fn):
                return await asyncio.gather(*(fn(x) for x in items))

            def fake_agent(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
                async def _call(call_prompt=None, call_schema=None):
                    txt = call_prompt or prompt or ""
                    if "Peer review" in txt:
                        return [{"label": "A", "accuracy": 9, "insight": 9, "critique": "Strong"}]
                    return f"Mocked response ({role})"

                def _factory(call_prompt=None, call_schema=None):
                    return _call(call_prompt, call_schema)

                if schema is not None and prompt is not None and "Independent answer" in prompt or (
                    role and ("Review" in role or "Chairman" in role)
                ):
                    return _call()
                return _factory

            env = {
                "args": {
                    "mode": "council",
                    "question": "Mocked workflow question",
                    "seats": ["inherit", "pro"],
                    "allow_tools": False,
                    "out_dir": tmp,
                },
                "phase": fake_phase,
                "log": fake_log,
                "parallel": fake_parallel,
                "agent": fake_agent,
            }
            wrapped = "async def __wf():\n" + "\n".join("    " + ln for ln in source.splitlines()) + "\n"
            code = compile(wrapped, WORKFLOW, "exec")
            exec(code, env)
            await env["__wf"]()
            runs = os.listdir(tmp)
            self.assertEqual(len(runs), 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, runs[0], "report.md")))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
