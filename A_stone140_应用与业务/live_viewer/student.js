"use strict";

const byId = id => document.getElementById(id);

function text(id, value) {
  const node = byId(id);
  if (node.textContent !== value) node.textContent = value;
}

function seatMessage(snapshot) {
  const state = snapshot?.state;
  if (snapshot?.phase === "ended") return ["演示已结束", "可以在「离线回放」中继续查看记录。", "waiting"];
  if (snapshot?.phase === "offline") return ["状态暂不可用", "暂时无法获取座位信息，请稍后再看。", "waiting"];
  if (snapshot?.phase === "interrupted" || snapshot?.silence_review || state?.review_required) {
    return ["待确认", "座位状态暂时无法确认，请先不要入座。", "waiting"];
  }
  if (!state || state.occupancy_pending || snapshot.phase !== "receiving") {
    return ["待确认", "正在确认座位状态，请稍等。", "waiting"];
  }
  if (snapshot.available) return ["空闲可用", "这个座位现在可以使用。", "free"];
  if (state.occupancy_state === "in_use") return ["有人使用", "座位正在使用，请选择其他空位。", "occupied"];
  if (state.occupancy_state === "leave_wait") return ["暂时离座", "座位暂为原使用者保留。", "waiting"];
  return ["待确认", "请稍后查看座位是否可用。", "waiting"];
}

function postureMessage(snapshot, seatTone) {
  if (snapshot?.capabilities && !snapshot.capabilities.includes("posture")) {
    return ["未启用", "这个座位当前仅提供占用提示。", "waiting"];
  }
  if (seatTone !== "occupied") return ["等待座位使用", "坐稳后可查看本座的坐姿反馈。", "waiting"];
  const state = snapshot.state;
  if (state.calibration_status !== "valid") {
    const message = state.calibration_status === "calibrating" ? "正在建立坐姿参考，请先保持自然坐姿。" : "等待有效的坐姿参考，当前暂停提醒。";
    return ["正在准备坐姿提醒", message, "waiting"];
  }
  if (state.posture_state === "reference") {
    return ["坐姿在参考范围", "当前可以继续自习。", "free"];
  }
  if (state.posture_state === "forward" || state.posture_state === "backward") {
    const forward = state.posture_state === "forward";
    if (state.reminder_state === "reminded") {
      return ["请调整坐姿", forward ? "检测到持续前倾，可以稍微坐直。" : "检测到持续后靠，可以回到自然坐姿。", "attention"];
    }
    return [forward ? "检测到前倾" : "检测到后靠", "如果这个姿势保持较久，页面会提示调整。", "waiting"];
  }
  return ["暂未识别坐姿", "请保持自然坐姿，等待下一次更新。", "waiting"];
}

function renderStudent(snapshot) {
  const state = snapshot?.state;
  const phase = snapshot?.phase;
  const replay = snapshot?.mode === "replay";
  const feedLabel = {
    receiving: replay ? (snapshot?.source_kind === "synthetic" ? "演示数据" : "录制回放") : "正在实时更新",
    waiting: "等待数据", offline: "服务未连接", interrupted: "更新已暂停", ended: "回放已结束",
  };
  text("feed", feedLabel[phase] ?? "连接中");
  byId("feed").dataset.tone = phase === "receiving" && !replay ? "active" : "waiting";
  text("seat-id", snapshot?.seat_id ? "座位 " + snapshot.seat_id : "");
  byId("seat-id").hidden = !snapshot?.seat_id;

  const [seatLabel, seatNote, seatTone] = seatMessage(snapshot);
  byId("seat-card").dataset.tone = seatTone;
  text("seat-status", seatLabel);
  text("seat-note", seatNote);
  const waitingToRelease = phase === "receiving" && !snapshot.silence_review && !state?.occupancy_pending &&
    !state?.review_required && state?.occupancy_state === "leave_wait" && typeof state.release_remaining_s === "number";
  byId("release").hidden = !waitingToRelease;
  if (waitingToRelease) text("release-time", Math.ceil(state.release_remaining_s) + " 秒");

  const [poseLabel, poseNote, poseTone] = postureMessage(snapshot, seatTone);
  byId("pose-card").dataset.tone = poseTone;
  text("pose-status", poseLabel);
  text("pose-note", poseNote);
  const elapsed = state?.posture_elapsed_s;
  const threshold = snapshot?.config?.remind_after_s;
  const timing = seatTone === "occupied" && snapshot.capabilities?.includes("posture") &&
    state.calibration_status === "valid" && ["forward", "backward"].includes(state.posture_state) &&
    ["accumulating", "reminded"].includes(state.reminder_state) && typeof elapsed === "number" && threshold > 0;
  byId("posture-timer").hidden = !timing;
  if (timing) {
    text("posture-time", elapsed.toFixed(1) + " 秒");
    byId("posture-progress").max = threshold;
    byId("posture-progress").value = Math.min(elapsed, threshold);
  }
}

let lastReading = null;
let controlBusy = false;
let requestVersion = 0;

function renderDetection(snapshot) {
  const status = snapshot?.detector;
  const button = byId("detect");
  const active = ["starting", "running", "stopping", "stop_failed"].includes(status);
  const messages = {
    unconfigured: "尚未配置本机雷达采集程序。",
    ready: "雷达待启动。请确认设备已连接本机。",
    starting: "正在启动雷达并等待第一帧。",
    running: snapshot?.phase === "interrupted" ? "雷达数据暂时中断。" : "正在检测。",
    stopping: "正在停止检测和保存采集数据。",
    stopped: "检测已停止。",
    stop_failed: "检测程序尚未退出。请再次停止或检查启动窗口。",
    exited: "检测程序已退出，可重新启动。",
    launch_failed: "检测程序启动失败。请检查本机配置和启动窗口。",
    no_frames: "启动后未收到数据，已停止。请检查设备连接和采集程序。",
    external: "当前由本机采集程序控制。",
    replay: "当前为录制回放。",
    offline: "本机监测服务未连接。",
  };
  button.dataset.action = active ? "stop" : "start";
  button.disabled = controlBusy || !status || ["stopping", "offline", "external", "replay"].includes(status);
  text("detect-label", active ? "停止检测" : "开始检测");
  text("detect-status", messages[status] ?? "正在连接本机监测服务。");
}

byId("detect").addEventListener("click", async () => {
  const action = byId("detect").dataset.action;
  requestVersion++;
  controlBusy = true;
  renderDetection(lastReading);
  try {
    const response = await fetch("/api/detection/" + action, {
      method: "POST", headers: {"X-Radar-Control": "1"}, signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("Detection control unavailable");
    const {detector} = await response.json();
    if (["starting", "stopped"].includes(detector)) {
      lastReading = {mode: "live", phase: "waiting", detector};
      renderStudent(lastReading);
    } else {
      lastReading = {...lastReading, detector};
    }
  } catch {
    text("detect-status", "操作未获确认。正在重新查询本机状态。");
    return;
  } finally {
    controlBusy = false;
  }
  renderDetection(lastReading);
});

async function refreshStudent() {
  const version = requestVersion;
  try {
    const response = await fetch("/api/state", {cache: "no-store", signal: AbortSignal.timeout(3000)});
    if (!response.ok) throw new Error("Could not read seat state");
    const reading = await response.json();
    if (version !== requestVersion || controlBusy) return;
    lastReading = reading;
    renderStudent(lastReading);
    renderDetection(lastReading);
  } catch {
    if (version !== requestVersion || controlBusy) return;
    renderStudent({...lastReading, phase: "offline"});
    renderDetection({detector: "offline"});
  } finally {
    setTimeout(refreshStudent, document.hidden ? 1600 : 400);
  }
}

refreshStudent();
