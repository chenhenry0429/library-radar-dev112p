"""实时页面的接收情况和反馈记录。依赖已接受的帧，不补造占用证据。"""
from collections import deque


class LiveFeedback:
    def __init__(self, config, speed):
        self.timeout = config["data_gap_s"] / speed
        self.review_after = config["review_after_s"] / speed
        self.events = deque(maxlen=100)
        self.evidence = None
        self.interrupted = False
        self.review = False

    def note(self, state, event_type, reason):
        self.events.append({
            "timestamp": state["timestamp"], "frame_id": state["frame_id"],
            "session_id": state["session_id"], "event_type": event_type, "reason": reason,
        })

    def observe_silence(self, state, received_at, now, phase):
        if received_at is None or phase != "receiving":
            return
        age = now - received_at
        if age > self.timeout and not self.interrupted:
            self.interrupted = True
            self.note(state, "feed_interrupted", "超过缺口阈值未收到新帧。已冻结计时，并从可用座位中排除。")
        if self.interrupted and age >= self.review_after and not self.review:
            self.review = True
            self.note(state, "feed_review", "长时间未收到新帧，请检查采集和算法进程。")

    def accept(self, previous, state, row, state_events):
        if self.interrupted:
            self.note(state, "feed_resumed", "已收到新帧，座位状态按当前证据重新确认。")
        self.interrupted = self.review = False
        self.events.extend(state_events)
        self.evidence = {k: row[k] for k in ("presence", "presence_confidence", "target_count", "posture",
                                                "posture_confidence", "quality")}
        if previous is None:
            return
        if previous["occupancy_pending"] != state["occupancy_pending"]:
            if state["occupancy_pending"]:
                self.note(state, "confirmation_pending", "占用证据待确认，暂不计入可用座位。")
            else:
                self.note(state, "confirmation_complete", "占用证据已重新确认。")
        if previous["calibration_status"] != state["calibration_status"]:
            self.note(state, "calibration_change", "个人标定状态已更新。")
            self.events[-1]["to_state"] = state["calibration_status"]
