import unittest
from pathlib import Path

import yaml

import json
import tempfile

from tools.run_campaign import check_planned, planned_runs

ROOT = Path(__file__).resolve().parents[1]


class CampaignPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load((ROOT / "cluster" / "case_studies.yaml").read_text(encoding="utf-8"))

    def test_every_admm_run_has_budget_tolerance_and_one_rank_per_agent(self):
        runs = planned_runs(self.config, set())
        admm = [r for r in runs if r["agents"] is not None]
        self.assertTrue(admm)
        for run in admm:
            args = run["args"]
            self.assertEqual(args[args.index("--agents") + 1], str(run["agents"]))
            self.assertEqual(args[args.index("--batch") + 1], str(run["agents"]))
            self.assertIn("--abs-tol", args)
            self.assertIn("--rel-tol", args)
            self.assertIn("--iterations", args)

    def test_repeats_and_unique_names(self):
        runs = planned_runs(self.config, {"C1_partitions"})
        self.assertEqual(len(runs), 4 * 3)
        self.assertEqual(len({r["name"] for r in runs}), len(runs))

    def test_order_rotates_between_repetitions(self):
        runs = planned_runs(self.config, {"C1_partitions"})
        first = [r["agents"] for r in runs[:4]]
        second = [r["agents"] for r in runs[4:8]]
        self.assertEqual(sorted(first), sorted(second))
        self.assertNotEqual(first, second)

    def test_campaign_tolerance_is_documented_choice(self):
        self.assertEqual(self.config["tolerances"]["campaign"], {"abs": 1.0e-4, "rel": 1.0e-4})
        strict = planned_runs(self.config, {"C3_strict_tolerance"})
        self.assertTrue(all(r["args"][r["args"].index("--abs-tol") + 1] == "1e-05" for r in strict))


class CampaignResumeTests(unittest.TestCase):
    SPEC = {"args": ["--agents", "3", "--rho", "3e5"], "extra": ["--scratch"]}

    def test_matching_plan_may_be_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.planned.json"
            path.write_text(json.dumps(self.SPEC), encoding="utf-8")
            check_planned(path, self.SPEC)

    def test_changed_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.planned.json"
            path.write_text(json.dumps(self.SPEC), encoding="utf-8")
            changed = {"args": ["--agents", "3", "--rho", "1e6"], "extra": ["--scratch"]}
            with self.assertRaises(SystemExit):
                check_planned(path, changed)

    def test_result_without_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                check_planned(Path(tmp) / "missing.planned.json", self.SPEC)


if __name__ == "__main__":
    unittest.main()
