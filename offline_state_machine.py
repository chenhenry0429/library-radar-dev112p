"""A stage: deterministic, data-time-only business state, independent of labels/raw."""
from __future__ import annotations
import csv
import math
from pathlib import Path
from contract import ContractError, active_calibration, load_c, paths, read_json, sha256, write_json

VERSION = "offline-A-1.0.0"
TIMELINE_COLUMNS = "frame_id,timestamp,seat_id,session_id,occupancy_state,occupancy_pending,posture_state,calibration_status,posture_elapsed_s,release_remaining_s,reminder_state,review_required".split(",")
EVENT_COLUMNS = "timestamp,frame_id,seat_id,session_id,event_type,from_state,to_state,reason".split(",")
DEFAULT_CONFIG = dict(presence_confirm_s=2.0, absence_confirm_s=3.0, release_wait_s=60.0,
    posture_confirm_s=2.0, remind_after_s=10.0, reference_reset_s=3.0, unknown_clear_s=3.0,
    cooldown_s=60.0, review_after_s=30.0, data_gap_s=None,
    presence_confidence_min=None, posture_confidence_min=None)


def validate_config(config):
    if set(config) != set(DEFAULT_CONFIG):
        raise ContractError("config.json: fields must match config_template.json exactly")
    for k, v in config.items():
        if k.endswith("_confidence_min") and v is None:
            continue
        if not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v):
            raise ContractError(f"config.json: {k} requires finite number; set data_gap_s explicitly")
        if k.endswith("_confidence_min"):
            if not 0 <= v <= 1:
                raise ContractError(f"config.json: {k} must be [0,1] or null")
        elif v <= 0:
            raise ContractError(f"config.json: {k} must be positive")
    return dict(config)


class StateMachine:
    def __init__(self, meta, algorithm, config):
        self.meta, self.alg, self.c = meta, algorithm, validate_config(config)
        self.state = "unknown"
        self.pending = True
        self.review = False
        self.unknown_since = None
        self.needs_confirm = True
        self.gap_wait_reset = False
        self.evidence = None
        self.evidence_since = None
        self.prev_t = None
        self.last_abs_accepted = False
        self.wait_elapsed = 0.0
        self.session = ""
        self.session_counter = 0
        self.sessions = []
        self.reference_after = math.inf
        self.pose = "not_applicable"
        self.pose_candidate = None
        self.pose_since = None
        self.pose_unknown_since = None
        self.event_pose = None
        self.pose_elapsed = 0.0
        self.pose_reminded = False
        self.last_pose_counted = False
        self.last_reminder = None
        self.events, self.timeline = [], []

    def emit(self, typ, before, after, reason):
        self.events.append(dict(timestamp=self.t, frame_id=self.fid, seat_id=self.meta["seat_id"],
            session_id=self.session, event_type=typ, from_state=before, to_state=after, reason=reason))

    def occupancy(self, state, reason):
        before = self.state
        if before != state:
            self.state = state
            self.emit("occupancy_change", before, state, reason)

    def posture(self, pose, reason):
        if self.pose != pose:
            before, self.pose = self.pose, pose
            self.emit("posture_change", before, pose, reason)

    def clear_pose_event(self):
        self.event_pose = None
        self.pose_elapsed = 0.0
        self.pose_reminded = False
        self.last_pose_counted = False

    def reset_personal(self, reason):
        self.reference_after = self.t
        self.clear_pose_event()
        self.pose_candidate, self.pose_since, self.pose_unknown_since = None, None, None
        self.emit("calibration_required", "", "uncalibrated", reason)

    def begin_session(self):
        self.session_counter += 1
        self.session = f"{self.meta['recording_id']}-seat-{self.session_counter:04d}"
        self.sessions.append(dict(session_id=self.session, start_timestamp=self.t,
                                  end_timestamp=None, complete=False))
        self.reset_personal("new confirmed seat session; reference must be acquired after this timestamp")

    def set_review(self):
        if not self.review:
            self.review = True
            self.emit("review_required", "0", "1", "continuous unknown evidence reaches review_after_s")

    def evidence_value(self, row):
        threshold = self.c["presence_confidence_min"]
        if row["quality"] != "ok" or (threshold is not None and
            (row["presence_confidence"] is None or row["presence_confidence"] < threshold)):
            return -1
        return row["presence"]

    def step_occupancy(self, p, gap, dt):
        if gap:
            self.emit("data_gap", self.state, self.state, f"gap {dt!r}s exceeds {self.c['data_gap_s']!r}s; entire interval excluded")
            self.evidence = self.evidence_since = None
            self.needs_confirm = True
            self.gap_wait_reset = True
            self.last_abs_accepted = False
            if self.unknown_since is None:
                self.unknown_since = self.prev_t
        if p == -1:
            self.pending = True
            self.needs_confirm = True
            self.evidence = self.evidence_since = None
            self.last_abs_accepted = False
            if self.unknown_since is None:
                self.unknown_since = self.t
            if self.t - self.unknown_since >= self.c["review_after_s"]:
                self.set_review()
            return
        if self.unknown_since is not None and self.t - self.unknown_since >= self.c["review_after_s"]:
            self.set_review()
        if self.evidence != p:
            self.evidence, self.evidence_since = p, self.t
        threshold = self.c["presence_confirm_s"] if p == 1 else self.c["absence_confirm_s"]
        matching = (p == 1 and self.state == "in_use") or (p == 0 and self.state in {"idle", "released", "leave_wait"})
        accepted = matching and not self.needs_confirm
        if self.t - self.evidence_since >= threshold - 1e-12:
            accepted = True
        if not accepted:
            self.pending = True
            self.last_abs_accepted = False
            return
        self.pending = False
        self.needs_confirm = False
        self.unknown_since = None
        if self.review:
            self.review = False
            self.emit("review_required", "1", "0", "valid evidence reconfirmed")
        if p == 1:
            if self.state in {"unknown", "idle", "released"}:
                self.begin_session()
                self.occupancy("in_use", "continuous valid presence confirmed")
            elif self.state == "leave_wait":
                self.occupancy("in_use", "presence reconfirmed; same seat session resumed")
                self.reset_personal("return after leave_wait; acquire a new personal reference")
            self.wait_elapsed = 0.0
            self.gap_wait_reset = False
            self.last_abs_accepted = False
        else:
            if self.state == "unknown":
                self.occupancy("idle", "continuous valid absence confirmed")
            if self.state == "in_use":
                self.wait_elapsed = 0.0
                self.occupancy("leave_wait", "continuous valid absence confirmed; waiting starts now")
                self.reference_after = math.inf
                self.clear_pose_event()
                self.last_abs_accepted = False
            elif self.state == "leave_wait":
                if self.gap_wait_reset:
                    self.wait_elapsed = 0.0
                elif self.last_abs_accepted and not gap:
                    self.wait_elapsed += dt
            self.gap_wait_reset = False
            if self.state == "leave_wait" and self.wait_elapsed >= self.c["release_wait_s"] - 1e-12:
                self.occupancy("released", "accumulated valid absence waiting reached release_wait_s")
                self.sessions[-1].update(end_timestamp=self.t, complete=True)
                self.reference_after = math.inf
                self.clear_pose_event()
                self.session = ""
            self.last_abs_accepted = self.state == "leave_wait"

    def step_posture(self, row, gap, dt):
        cal = active_calibration(self.alg, self.t)
        effective = row["calibration_status"]
        personal_valid = (self.state == "in_use" and effective == "valid" and cal is not None
                          and cal["start_timestamp"] >= self.reference_after)
        if effective == "valid" and not personal_valid:
            effective = "invalid"
        if self.state != "in_use":
            self.posture("not_applicable", "seat is not in_use")
            self.pose_candidate = self.pose_since = self.pose_unknown_since = None
            self.last_pose_counted = False
            return effective, "idle"
        pose = row["posture"]
        pc = self.c["posture_confidence_min"]
        usable = (not self.pending and row["presence"] == 1 and row["quality"] == "ok" and personal_valid
                  and pose in {"reference", "forward", "backward"}
                  and (pc is None or (row["posture_confidence"] is not None and row["posture_confidence"] >= pc)))
        if gap:
            self.pose_candidate = self.pose_since = None
            self.last_pose_counted = False
            if self.pose_unknown_since is None:
                self.pose_unknown_since = self.prev_t
        if not usable:
            self.posture("unknown", "posture/occupancy evidence unavailable or current personal reference invalid")
            self.pose_candidate = self.pose_since = None
            self.last_pose_counted = False
            if self.pose_unknown_since is None:
                self.pose_unknown_since = self.t
            if self.t - self.pose_unknown_since > self.c["unknown_clear_s"]:
                self.clear_pose_event()
            return effective, "paused"
        if self.pose_unknown_since is not None and self.t - self.pose_unknown_since > self.c["unknown_clear_s"]:
            self.clear_pose_event()
        self.pose_unknown_since = None
        if self.pose_candidate != pose:
            self.pose_candidate, self.pose_since = pose, self.t
            self.last_pose_counted = False
            if pose in {"forward", "backward"} and self.event_pose != pose:
                self.clear_pose_event()
        needed = self.c["reference_reset_s"] if pose == "reference" else self.c["posture_confirm_s"]
        if self.t - self.pose_since < needed - 1e-12:
            self.posture("unknown", "waiting for stable posture evidence")
            return effective, "paused"
        self.posture(pose, "stable posture confirmed")
        if pose == "reference":
            self.clear_pose_event()
            return effective, "idle"
        if self.event_pose != pose:
            self.event_pose, self.pose_elapsed, self.pose_reminded = pose, 0.0, False
        elif self.last_pose_counted and not gap:
            self.pose_elapsed += dt
        self.last_pose_counted = True
        if self.pose_reminded:
            return effective, "reminded"
        cooled = self.last_reminder is None or self.t - self.last_reminder >= self.c["cooldown_s"] - 1e-12
        if self.pose_elapsed >= self.c["remind_after_s"] - 1e-12 and cooled:
            self.last_reminder, self.pose_reminded = self.t, True
            self.emit("reminder", "accumulating", "reminded", f"{pose}: confirmed posture duration reached remind_after_s; no audio")
            return effective, "reminded"
        return effective, "accumulating" if cooled else "cooldown"

    def step(self, row):
        self.t, self.fid = row["timestamp"], row["frame_id"]
        dt = 0.0 if self.prev_t is None else self.t - self.prev_t
        gap = dt > self.c["data_gap_s"]
        self.step_occupancy(self.evidence_value(row), gap, dt)
        cal, remind = self.step_posture(row, gap, dt)
        self.timeline.append(dict(frame_id=self.fid, timestamp=self.t, seat_id=self.meta["seat_id"],
            session_id=self.session, occupancy_state=self.state, occupancy_pending=int(self.pending),
            posture_state=self.pose, calibration_status=cal,
            posture_elapsed_s=self.pose_elapsed if self.state == "in_use" and self.event_pose is not None else None,
            release_remaining_s=max(0.0, self.c["release_wait_s"] - self.wait_elapsed) if self.state == "leave_wait" else None,
            reminder_state=remind, review_required=int(self.review)))
        self.prev_t = self.t

    def run(self, rows):
        for row in rows:
            self.step(row)
        return self.timeline, self.events


def _write_csv(path, columns, rows):
    with Path(path).open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_a(folder, config_path, output_root, run_id):
    from datetime import datetime, timezone
    from contract import recording_id
    recording_id(run_id)
    p = paths(folder)
    inputs = {k: p[k] for k in ("result", "meta", "algorithm")}
    inputs["config"] = Path(config_path)
    before = {k: sha256(v) for k, v in inputs.items()}
    rows, meta, alg = load_c(folder)  # raw used only to validate alignment; engine never receives it
    config = validate_config(read_json(config_path))
    machine = StateMachine(meta, alg, config)
    timeline, events = machine.run(rows)
    if before != {k: sha256(v) for k, v in inputs.items()}:
        raise ContractError("input changed during processing; output not committed")
    out = Path(output_root) / meta["recording_id"] / run_id
    out.mkdir(parents=True, exist_ok=False)
    _write_csv(out / "timeline.csv", TIMELINE_COLUMNS, timeline)
    _write_csv(out / "events.csv", EVENT_COLUMNS, events)
    write_json(out / "run.json", dict(schema_version="1.0", recording_id=meta["recording_id"],
        run_id=run_id, source_kind=meta["source_kind"], program_version=VERSION,
        created_at=datetime.now(timezone.utc).isoformat(), algorithm_version=alg["algorithm_version"],
        inputs={k: dict(path=str(v.resolve()), sha256=before[k]) for k, v in inputs.items()},
        validation_raw=dict(path=str(p["raw"].resolve()), sha256=sha256(p["raw"]), use="alignment validation only"),
        parameters=config, end_timestamp=rows[-1]["timestamp"], sessions=machine.sessions,
        session_complete=all(s["complete"] for s in machine.sessions), eof_policy="stop; do not advance data time",
        calibration_policy="new reference.start_timestamp >= confirmed entry/return timestamp",
        free_at_eof=machine.state in {"idle", "released"} and not machine.pending and not machine.review))
    return out
