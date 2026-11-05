"""Meaningful v1 contract, time-boundary and offline handover regression checks."""
from __future__ import annotations
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import numpy as np
from contract import (ContractError, load_b, load_c, paths, save_b, validate_raw,
                      write_json, write_result)
from offline_state_machine import DEFAULT_CONFIG, StateMachine, run_a, validate_config
from radar_offline import (calibration, evidence, make_demo, pack, synthetic_algorithm, synthetic_meta)
from verify_transfer import verify


class StateTests(unittest.TestCase):
    def engine(self, rows, cals=None, **changes):
        meta = dict(recording_id="20261103_PDEMO_S01_mixed_99", seat_id="SEAT01")
        cfg = dict(DEFAULT_CONFIG, data_gap_s=1.5)
        cfg.update(changes)
        alg = synthetic_algorithm(meta["recording_id"], cals or [calibration("C1", 3, 4)])
        m = StateMachine(meta, alg, cfg)
        m.run(rows)
        return m

    def rows(self, times, rule):
        out = []
        for i, t in enumerate(times):
            p, pose = rule(t)
            out.append(evidence(i, t, p, pose, "valid" if t >= 4 else "uncalibrated"))
        return out

    def ev(self, m, typ):
        return [e for e in m.events if e["event_type"] == typ]

    def test_initial_unknown_not_idle(self):
        m = self.engine([evidence(i, i, -1) for i in range(4)])
        self.assertTrue(all(r["occupancy_state"] == "unknown" for r in m.timeline))
        self.assertFalse(m.sessions)

    def test_entry_and_absence_confirmation(self):
        m = self.engine(self.rows(range(8), lambda t: (1, "unknown") if t < 3 else (0, "not_applicable")))
        self.assertEqual([(e["timestamp"], e["to_state"]) for e in self.ev(m, "occupancy_change")], [(2., "in_use"), (6., "leave_wait")])
        self.assertEqual(m.timeline[-1]["release_remaining_s"], 59.)

    def test_release_exact_valid_wait_and_eof(self):
        rows = self.rows(range(67), lambda t: (1, "unknown") if t < 3 else (0, "not_applicable"))
        m = self.engine(rows)
        self.assertEqual(m.timeline[-2]["occupancy_state"], "leave_wait")
        self.assertEqual(m.timeline[-1]["occupancy_state"], "released")
        self.assertEqual(m.sessions[0]["end_timestamp"], 66.)
        self.assertTrue(m.sessions[0]["complete"])
        cut = self.engine(rows[:20])
        self.assertEqual(cut.state, "leave_wait")
        self.assertFalse(cut.sessions[0]["complete"])
        self.assertIsNone(cut.sessions[0]["end_timestamp"])

    def test_passer_does_not_create_session(self):
        m = self.engine(self.rows(range(12), lambda t: (1, "unknown") if t in (4, 5) else (0, "not_applicable")))
        self.assertFalse(m.sessions)
        self.assertEqual(m.state, "idle")

    def test_pose_confirmation_then_ten_seconds(self):
        m = self.engine(self.rows(range(35), lambda t: (1, "forward" if t >= 5 else "unknown")))
        reminders = self.ev(m, "reminder")
        self.assertEqual([e["timestamp"] for e in reminders], [17.])
        self.assertEqual(m.timeline[7]["posture_elapsed_s"], 0.)
        self.assertEqual(m.timeline[17]["posture_elapsed_s"], 10.)

    def test_short_pose_unknown_pauses_same_event(self):
        m = self.engine(self.rows(range(40), lambda t: (1, "unknown" if t < 5 or t == 10 else "forward")))
        self.assertFalse(any(r["occupancy_pending"] for r in m.timeline[5:]))
        self.assertEqual(m.timeline[10]["reminder_state"], "paused")
        self.assertEqual(m.timeline[10]["posture_elapsed_s"], 2.)
        self.assertEqual([e["timestamp"] for e in self.ev(m, "reminder")], [21.])

    def test_long_pose_unknown_clears_after_strictly_three(self):
        m = self.engine(self.rows(range(40), lambda t: (1, "unknown" if t < 5 or 10 <= t <= 14 else "forward")))
        self.assertEqual(m.timeline[13]["posture_elapsed_s"], 2.)
        self.assertIsNone(m.timeline[14]["posture_elapsed_s"])
        self.assertEqual([e["timestamp"] for e in self.ev(m, "reminder")], [27.])

    def test_reference_three_seconds_resets(self):
        m = self.engine(self.rows(range(40), lambda t: (1, "unknown" if t < 5 else ("reference" if 10 <= t <= 14 else "forward"))))
        self.assertEqual(m.timeline[12]["posture_elapsed_s"], 2.)
        self.assertIsNone(m.timeline[13]["posture_elapsed_s"])
        self.assertEqual([e["timestamp"] for e in self.ev(m, "reminder")], [27.])

    def test_switch_posture_cannot_bypass_cooldown(self):
        m = self.engine(self.rows(range(100), lambda t: (1, "unknown" if t < 5 else ("forward" if t < 20 else "backward"))))
        self.assertEqual([e["timestamp"] for e in self.ev(m, "reminder")], [17., 77.])
        self.assertEqual(m.timeline[32]["reminder_state"], "cooldown")

    def test_return_same_session_requires_new_reference(self):
        rows = self.rows(range(40), lambda t: (0, "not_applicable") if 8 <= t < 15 else (1, "forward" if t >= 5 else "unknown"))
        m = self.engine(rows)
        self.assertEqual(len(m.sessions), 1)
        self.assertEqual([e["timestamp"] for e in self.ev(m, "calibration_required")], [2., 17.])
        self.assertEqual(m.timeline[18]["calibration_status"], "invalid")
        self.assertFalse(self.ev(m, "reminder"))
        m2 = self.engine(rows, [calibration("C1", 3, 4, 8.), calibration("C2", 18, 20)])
        self.assertEqual(m2.timeline[20]["calibration_status"], "valid")
        self.assertEqual([e["timestamp"] for e in self.ev(m2, "reminder")], [32.])

    def test_unknown_presence_pauses_wait_does_not_release(self):
        rows = self.rows(range(100), lambda t: ((1, "unknown") if t < 3 else ((-1, "unknown") if t >= 10 else (0, "not_applicable"))))
        m = self.engine(rows)
        self.assertEqual(m.state, "leave_wait")
        self.assertEqual(m.timeline[9]["release_remaining_s"], m.timeline[-1]["release_remaining_s"])
        self.assertTrue(m.review)
        self.assertFalse(m.sessions[0]["complete"])
        self.assertEqual([e["timestamp"] for e in self.ev(m, "review_required")], [40.])

    def test_gap_excluded_review_reconfirm_wait_restarts(self):
        times = list(range(11)) + list(range(51, 56))
        m = self.engine(self.rows(times, lambda t: (1, "unknown") if t < 3 else (0, "not_applicable")))
        self.assertEqual(len(self.ev(m, "data_gap")), 1)
        self.assertEqual(m.timeline[11]["review_required"], 1)
        self.assertEqual(m.timeline[11]["release_remaining_s"], 56.)
        self.assertEqual(m.timeline[14]["release_remaining_s"], 60.)
        self.assertEqual(m.timeline[-1]["release_remaining_s"], 59.)
        self.assertEqual(m.state, "leave_wait")

    def test_low_confidence_is_unknown_and_nullable_gate(self):
        rows = [evidence(i, i, 0) for i in range(8)]
        for r in rows:
            r["presence_confidence"] = .2
        self.assertEqual(self.engine(rows, presence_confidence_min=.8).state, "unknown")
        self.assertEqual(self.engine(rows).state, "idle")

    def test_explicit_data_gap_required(self):
        with self.assertRaises(ContractError):
            validate_config(DEFAULT_CONFIG)

    def test_short_unknown_wait_pauses_then_reconfirms_without_reset(self):
        rows = self.rows(range(20), lambda t: (1, "unknown") if t < 3 else ((-1, "unknown") if t in (10, 11) else (0, "not_applicable")))
        m = self.engine(rows)
        self.assertEqual(m.timeline[9]["release_remaining_s"], 57.)
        self.assertEqual(m.timeline[15]["release_remaining_s"], 57.)
        self.assertEqual(m.timeline[16]["release_remaining_s"], 56.)

    def test_new_session_after_release_and_eof_no_extra_event(self):
        rows = self.rows(range(72), lambda t: (1, "unknown") if t < 3 or t >= 67 else (0, "not_applicable"))
        m = self.engine(rows)
        self.assertEqual(len(m.sessions), 2)
        self.assertTrue(m.sessions[0]["complete"])
        self.assertFalse(m.sessions[1]["complete"])
        self.assertEqual(m.sessions[1]["start_timestamp"], 69.)
        self.assertTrue(all(e["timestamp"] <= 71 for e in m.events))

    def test_posture_low_confidence_keeps_occupancy(self):
        rows = self.rows(range(30), lambda t: (1, "forward" if t >= 5 else "unknown"))
        for r in rows:
            if r["posture_confidence"] is not None:
                r["posture_confidence"] = .2
        m = self.engine(rows, posture_confidence_min=.8)
        self.assertEqual(m.state, "in_use")
        self.assertFalse(m.pending)
        self.assertFalse(self.ev(m, "reminder"))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, raw=None, times=None):
        times = np.array([0., .1, np.nextafter(.2, 1)], dtype=np.float64) if times is None else times
        raw = np.arange(len(times) * 12, dtype=np.int16).reshape(len(times), 2, 2, 3) if raw is None else raw
        rid = "20261103_PDEMO_S01_sit_01"
        meta = synthetic_meta(rid, raw)
        meta["axis_order"] = ["frame"] + [f"fixture_axis_{i}" for i in range(1, raw.ndim)]
        folder = save_b(self.root / "b", meta, raw, times)
        rows = [evidence(i, t, 0) for i, t in enumerate(times)]
        write_result(paths(folder)["result"], rows)
        alg = synthetic_algorithm(rid, [])
        alg["capabilities"]["posture"] = False
        write_json(paths(folder)["algorithm"], alg)
        return folder

    def test_multidimensional_dtype_and_precision_roundtrip(self):
        f = self.fixture()
        a, _ = load_b(f)
        self.assertEqual(a["raw_data"].shape, (3, 2, 2, 3))
        self.assertEqual(a["raw_data"].dtype, np.dtype("int16"))
        rows, _, _ = load_c(f)
        self.assertEqual(rows[-1]["timestamp"], a["timestamps"][-1])
        self.assertNotEqual(rows[-1]["timestamp"], .2)

    def test_complex_values_not_lost(self):
        raw = (np.arange(18).reshape(3, 2, 3) + 2j).astype(np.complex64)
        f = self.fixture(raw=raw)
        a, _ = load_b(f)
        np.testing.assert_array_equal(a["raw_data"], raw)
        self.assertEqual(a["raw_data"].dtype, raw.dtype)

    def test_reject_timestamp_duplicates_and_reversal(self):
        for t in ([0., .1, .1], [0., .2, .1], [-1., 0., 1.]):
            with self.assertRaisesRegex(ContractError, "row"):
                self.fixture(times=np.array(t, dtype=np.float64))

    def test_reject_object_payload_and_missing_layout(self):
        raw = np.array([[object()], [object()], [object()]], dtype=object)
        with self.assertRaisesRegex(ContractError, "numeric"):
            self.fixture(raw=raw)
        f = self.fixture()
        a, meta = load_b(f)
        meta["axis_order"] = None
        with self.assertRaisesRegex(ContractError, "axis_order"):
            validate_raw(a, meta)

    def mutate_csv(self, transform):
        f = self.fixture()
        p = paths(f)["result"]
        rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8"))))
        transform(rows)
        with p.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
        return f

    def test_reject_mismatched_timestamp_with_filename_row(self):
        f = self.mutate_csv(lambda r: r[2].__setitem__(1, "0.10000001"))
        with self.assertRaisesRegex(ContractError, r"result.csv.*CSV row 3"):
            load_c(f)

    def test_reject_missing_rows(self):
        f = self.mutate_csv(lambda r: r.pop())
        with self.assertRaisesRegex(ContractError, "missing result row"):
            load_c(f)

    def test_reject_header_and_enum_and_unknown_as_absent(self):
        mutations = [lambda r: r[0].__setitem__(2, "wrong"), lambda r: r[1].__setitem__(7, "bad"),
                     lambda r: r[1].__setitem__(8, "low_signal")]
        for i, mutate in enumerate(mutations):
            self.root = Path(self.tmp.name) / str(i)
            f = self.mutate_csv(mutate)
            with self.assertRaises(ContractError):
                load_c(f)

    def test_demo_exact_format_and_determinism_and_labels_isolation(self):
        folder, out = make_demo(self.root / "demo")
        f2 = folder.parent / "20261103_PDEMO_S01_mixed_02"
        _, _, _ = load_c(f2)
        lines = paths(f2)["result"].read_text().splitlines()
        self.assertEqual(lines[1], "0,0.0,-1,,-1,unknown,,uncalibrated,low_signal")
        self.assertEqual(lines[-1], "4,0.4,-1,,-1,unknown,,invalid,invalid_frame")
        (folder / (folder.name + "_labels.csv")).write_text("THIS MUST NEVER BE READ", encoding="utf-8")
        again = run_a(folder, folder.parent / "demo_config.json", folder.parent / "output", "repeat")
        for name in ("timeline.csv", "events.csv"):
            self.assertEqual((out / name).read_bytes(), (again / name).read_bytes())
        self.assertFalse(json.loads((out / "run.json").read_text())["session_complete"])

    def test_zip_verification_and_corruption_detection(self):
        f = self.fixture()
        target = pack(f, self.root / "delivery.zip")
        self.assertEqual(verify(target)["source_kind"], "synthetic")
        with zipfile.ZipFile(target) as src, zipfile.ZipFile(self.root / "corrupt.zip", "w") as dest:
            for name in src.namelist():
                data = src.read(name)
                if name.endswith("_meta.json"):
                    data += b" "
                dest.writestr(name, data)
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            verify(self.root / "corrupt.zip")

    def test_b_only_zip_and_no_upstream_overwrite(self):
        f = self.fixture()
        target = pack(f, self.root / "b_only.zip", "b")
        manifest = verify(target)
        self.assertEqual(manifest["stage"], "b")
        self.assertFalse(any(n.endswith("_result.csv") or n.endswith("_algorithm.json") for n in manifest["files"]))
        before = paths(f)["raw"].read_bytes()
        arrays, meta = load_b(f)
        with self.assertRaises(FileExistsError):
            save_b(f.parent, meta, arrays["raw_data"], arrays["timestamps"])
        self.assertEqual(paths(f)["raw"].read_bytes(), before)

    def test_reject_retroactive_and_missing_calibration(self):
        from contract import validate_algorithm
        f = self.fixture()
        _, meta = load_b(f)
        alg = synthetic_algorithm(meta["recording_id"], [calibration("C1", 0, .2)])
        alg["calibrations"][0]["valid_from_timestamp"] = .1
        with self.assertRaisesRegex(ContractError, "no retroactive"):
            validate_algorithm(alg, meta)
        p = paths(f)
        p["algorithm"].write_text(json.dumps(synthetic_algorithm(meta["recording_id"], [])), encoding="utf-8")
        rows = [evidence(i, t, 1, "forward", "valid") for i, t in enumerate([0., .1, np.nextafter(.2, 1)])]
        p["result"].unlink()
        write_result(p["result"], rows)
        with self.assertRaisesRegex(ContractError, "no current calibration"):
            load_c(f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
