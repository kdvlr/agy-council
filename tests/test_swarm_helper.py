import ast
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

    def test_workflow_script_compiles(self) -> None:
        with open(WORKFLOW, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, WORKFLOW, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_council_mode_end_to_end(self) -> None:
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
            self.assertEqual(
                [x["model"] for x in r1["swarm_add"]],
                ["gemini-4-carbon-high", "claude-opus-4-6-thinking"],
            )
            run_dir = r1["run_dir"]
            for idx, path in enumerate(r1["expected_outputs"]):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Detailed response from seat {idx}.")

            r2 = self.run_helper("prepare-reviews", "--run-dir", run_dir)
            with open(r2["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write('```json\n[{"label": "A", "accuracy": 9, "insight": 10, "critique": "Strong depth"}]\n```')
            with open(r2["expected_outputs"][1], "w", encoding="utf-8") as f:
                f.write('[{"label": "A", "accuracy": 8, "insight": 8, "critique": "Accurate summary"}]')

            r3 = self.run_helper("prepare-chairman", "--run-dir", run_dir)
            self.assertEqual(r3["chairman_model"], "claude-opus-4-6-thinking")
            with open(r3["expected_outputs"][0], "w", encoding="utf-8") as f:
                f.write("Final chairman synthesis.")

            r4 = self.run_helper("finalize-council", "--run-dir", run_dir)
            self.assertEqual(r4["status"], "completed")
            self.assertTrue(os.path.exists(r4["report"]))

    def test_consensus_mode_early_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c1 = self.run_helper(
                "init",
                "--mode",
                "consensus",
                "--out-dir",
                tmp,
                "--question",
                "Write a backup plan.",
                "--max-rounds",
                "2",
            )
            run_dir = c1["run_dir"]
            with open(c1["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Initial v1", "plan": "# Plan\n1. Backup", "agreed": False}, f)

            c2 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertEqual(len(c2["swarm_add"]), 1)
            with open(c2["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump(
                    {"revision_notes": "Add verify step", "plan": "# Plan\n1. Backup\n2. Verify", "agreed": False},
                    f,
                )

            c3 = self.run_helper("step-consensus", "--run-dir", run_dir)
            with open(c3["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump(
                    {"revision_notes": "Approved as-is", "plan": "# Plan\n1. Backup\n2. Verify", "agreed": True},
                    f,
                )

            c4 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertEqual(c4["status"], "completed")
            self.assertTrue(c4["consensus"])

    def test_consensus_mode_deadlock_consolidation(self) -> None:
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
            with open(d1["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "v1", "plan": "Plan v1", "agreed": False}, f)

            d2 = self.run_helper("step-consensus", "--run-dir", run_dir)
            with open(d2["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "v2", "plan": "Plan v2", "agreed": False}, f)

            d3 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertTrue(d3.get("consolidation"))
            with open(d3["expected_outputs"][0], "w", encoding="utf-8") as f:
                json.dump({"revision_notes": "Merged", "plan": "Consolidated Plan", "agreed": True}, f)

            d4 = self.run_helper("step-consensus", "--run-dir", run_dir)
            self.assertEqual(d4["status"], "completed")
            self.assertFalse(d4["consensus"])


if __name__ == "__main__":
    unittest.main()
