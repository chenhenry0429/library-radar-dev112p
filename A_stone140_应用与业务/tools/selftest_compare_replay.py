"""CLI regression checks using the committed synthetic lifecycle recording."""

import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


TOOL = Path(__file__).with_name("compare_replay.py")
ROOT = TOOL.resolve().parents[2]
DEMO = ROOT / "DEMO_ONLY" / "release_demo"
RECORDING_ID = "20261103_PDEMO_S01_lifecycle_01"
DATA = DEMO / RECORDING_ID
RESULT = DATA / f"{RECORDING_ID}_result.csv"
META = DATA / f"{RECORDING_ID}_meta.json"
ALGORITHM = DATA / f"{RECORDING_ID}_algorithm.json"
CONFIG = DEMO / "demo_config.json"


class CompareReplayTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(prefix="compare-replay-test-")
        self.addCleanup(scratch.cleanup)
        self.work = Path(scratch.name)
        self.output = self.work / "output"

    def compare(self, *extra, baseline=RESULT, candidate=RESULT):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL), "--meta", str(META),
             "--baseline", str(baseline), "--baseline-algorithm", str(ALGORITHM),
             "--candidate", str(candidate), "--candidate-algorithm", str(ALGORITHM),
             "--config", str(CONFIG), "--output", str(self.output), *extra],
            cwd=self.work, capture_output=True, encoding="utf-8", timeout=30,
        )

    def summary(self):
        return json.loads((self.output / "comparison.json").read_text(encoding="utf-8"))

    def change_result(self, name, update):
        path = self.work / name
        with RESULT.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
            with path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
                writer.writeheader()
                writer.writerows(update(rows))
        return path

    def test_complete_lifecycle(self):
        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.summary()
        self.assertEqual(summary["baseline"], summary["candidate"])
        marks = summary["baseline"]["milestones"]
        self.assertEqual([e["frame_id"] for e in marks["leave_wait"]], [610, 1120])
        self.assertEqual([e["frame_id"] for e in marks["returned"]], [670])
        self.assertEqual([e["frame_id"] for e in marks["released"]], [1720])
        for name in ("baseline_timeline", "baseline_events", "candidate_timeline", "candidate_events"):
            self.assertTrue((self.output / f"{name}.csv").is_file())

    def test_common_prefix_must_be_explicit(self):
        shorter = self.change_result("prefix.csv", lambda rows: rows[:1200])
        different = self.compare(baseline=shorter)
        self.assertNotEqual(different.returncode, 0)
        self.assertIn("--frame-limit", different.stderr)
        self.assertFalse(self.output.exists())
        result = self.compare("--frame-limit", "1200", baseline=shorter)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.summary()
        self.assertEqual(summary["compared_frames"], 1200)
        self.assertEqual(summary["baseline_input_frames"], 1200)
        self.assertGreater(summary["candidate_input_frames"], 1200)
        self.assertEqual(summary["candidate"]["end_state"], "leave_wait")
        self.assertEqual(summary["candidate"]["milestones"]["released"], [])

    def test_time_mismatch_leaves_no_output(self):
        def shift_first_row(rows):
            rows[0]["timestamp"] = "0.001"
            return rows

        result = self.compare(candidate=self.change_result("shifted.csv", shift_first_row))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("时间戳不一致", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
