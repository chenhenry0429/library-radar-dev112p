"use strict";

const element = id => document.getElementById(id);
const setText = (id, text) => { element(id).textContent = text; };
const phases = {waiting: "等待首帧", receiving: "接收中", interrupted: "数据中断", ended: "回放结束", offline: "服务断开"};
const occupancy = {unknown: "未知", idle: "空闲", in_use: "使用中", leave_wait: "离座等待", released: "已释放"};
const posture = {unknown: "未知", not_applicable: "不适用", reference: "参考范围", forward: "前倾", backward: "后靠"};
const calibration = {uncalibrated: "未标定", calibrating: "标定中", valid: "有效", invalid: "已失效"};
const reminders = {idle: "未计时", accumulating: "计时中", reminded: "已提醒", cooldown: "冷却中", paused: "已暂停"};
const quality = {ok: "有效", low_signal: "低信号", invalid_frame: "无效帧"};
const presence = {"0": "无人", "1": "有人", "-1": "未知"};
const eventLabels = {occupancy_change: "占用变化", posture_change: "姿态变化", calibration_required: "需要标定",
  calibration_change: "标定变化", data_gap: "数据缺口", review_required: "人工复核", reminder: "姿态提醒",
  feed_interrupted: "接收中断", feed_resumed: "接收恢复", feed_review: "断流复核",
  confirmation_pending: "占用待确认", confirmation_complete: "确认恢复"};
const warnings = new Set(["calibration_required", "calibration_change", "data_gap", "review_required",
  "feed_interrupted", "feed_resumed", "feed_review", "confirmation_pending", "confirmation_complete"]);
const parameterNames = {
  presence_confirm_s: "入座确认", absence_confirm_s: "离座确认", release_wait_s: "释放等待",
  posture_confirm_s: "姿态确认", remind_after_s: "提醒门限", reference_reset_s: "正常姿态重置",
  unknown_clear_s: "未知姿态清除", cooldown_s: "提醒冷却", review_after_s: "复核等待",
  data_gap_s: "数据缺口", presence_confidence_min: "占用最低分数", posture_confidence_min: "姿态最低分数",
};

let lastSnapshot = null;
let shownSnapshot = null;
const rendered = new Map();

function replaceList(id, items, makeNode) {
  const key = JSON.stringify(items);
  if (rendered.get(id) === key) return;
  rendered.set(id, key);
  element(id).replaceChildren(...items.map(makeNode));
}

function seconds(value, suffix = "") {
  return typeof value === "number" ? value.toFixed(1) + " 秒" + suffix : "—";
}

function withScore(label, score) {
  return typeof score === "number" ? label + " · 分数 " + score.toFixed(2) : label;
}

function describeAlerts(phase, state, review, poseSupported, evidence) {
  const notices = [];
  if (phase === "waiting") notices.push(["info", "等待 C 端的第一帧结果。"]);
  if (phase === "interrupted") notices.push(["attention", "收帧中断。保留最后确认状态，暂停计时，不计入可用空位。"]);
  if (phase === "offline") notices.push(["attention", "页面与 A 端服务断开。请检查监测程序是否还在运行。"]);
  if (phase === "ended") notices.push(["info", state?.session_id ? "回放结束，保留尚未结束的占用会话。" : "回放结束，保留最后一帧记录。"]);
  if (phase === "receiving" && state?.occupancy_pending) notices.push(["attention", "占用待确认，暂不计入可用空位。"]);
  if (review && phase !== "ended") notices.push(["attention", "需要人工复核，请检查采集和识别结果。"]);
  if (phase === "receiving" && evidence && evidence.quality !== "ok") {
    notices.push(["attention", "当前信号质量为「" + quality[evidence.quality] + "」，这帧不能确认占用。"]);
  }
  if (poseSupported === false) notices.push(["info", "当前算法仅提供占用判断，坐姿提醒未启用。"]);
  if (phase === "receiving" && poseSupported && state?.occupancy_state === "in_use") {
    if (state.calibration_status !== "valid") {
      notices.push(["attention", "个人标定" + calibration[state.calibration_status] + "，姿态提醒等待有效标定。"]);
    } else if (state.posture_state === "unknown") {
      notices.push(["info", "姿态暂时未知，占用状态独立判定。"]);
    } else if (!state.occupancy_pending && !review && state.reminder_state === "reminded") {
      notices.push(["reminder", "持续" + posture[state.posture_state] + "达到提醒阈值，请调整坐姿。"]);
    }
  }
  return notices.length ? notices : [["info", "当前没有异常或姿态提醒。"]];
}

function renderEvents(entries) {
  const filter = element("event-filter").value;
  const visible = entries.filter(event => filter === "all" ||
    (filter === "warning" ? warnings.has(event.event_type) : filter === event.event_type)).slice().reverse();
  element("eventEmpty").hidden = visible.length > 0;
  setText("eventEmpty", entries.length ? "当前筛选没有匹配的事件" : "等待事件");
  replaceList("events", visible, event => {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = event.timestamp.toFixed(3) + " 秒";
    if (warnings.has(event.event_type)) item.dataset.tone = "attention";
    let description = eventLabels[event.event_type] ?? event.event_type;
    if (event.event_type === "calibration_change") description += " · " + calibration[event.to_state];
    const states = event.event_type === "occupancy_change" ? occupancy : event.event_type === "posture_change" ? posture : null;
    if (states?.[event.from_state] && states?.[event.to_state]) {
      description += " · " + states[event.from_state] + " → " + states[event.to_state];
    }
    item.append(time, description + " · " + event.reason);
    return item;
  });
}

function render(snapshot) {
  shownSnapshot = snapshot;
  const state = snapshot?.state;
  const phase = snapshot?.phase ?? "offline";
  const active = phase === "receiving";
  const ended = phase === "ended";
  const review = snapshot?.silence_review || state?.review_required;
  const pending = !active || !state || state.occupancy_pending || review;
  const available = active && snapshot.available;
  const poseSupported = snapshot?.capabilities?.includes("posture");
  const paused = pending && !ended;
  const evidence = active ? snapshot.evidence : null;
  setText("connection", phases[phase]);
  if (snapshot?.mode) {
    const origin = snapshot.source_kind === "synthetic" ? "合成数据" : "实测数据";
    const mode = snapshot.mode === "replay" ? "逐帧回放" : "在线接收";
    const timebase = snapshot.nominal_time ? " · 名义时间" : "";
    setText("source", mode + " · " + origin + timebase + " · " + snapshot.algorithm_version);
    setText("metadata", "录制 " + snapshot.recording_id + " · 参数版本 " + (snapshot.parameter_version ?? "—"));
  }
  setText("seat", snapshot?.seat_id ? "座位 " + snapshot.seat_id : "座位");
  setText("occupancy", state ? occupancy[state.occupancy_state] : "未知");
  setText("availability", ended ? "回放结束，保留最后记录" : review ? "需人工复核 · 不可用" :
    pending ? "待确认 · 不可用" : available ? "可用" : "不可用");
  setText("available-count", available ? "1" : "0");
  setText("pending-count", pending && !ended ? "1" : "0");
  element("seatPanel").dataset.tone = ended ? "history" : pending || state?.occupancy_state === "leave_wait" ? "attention" : "ready";
  setText("posture", poseSupported === false ? "未提供" : state ? (paused ? "未知" : posture[state.posture_state]) : "—");
  setText("calibration", poseSupported === false ? "不适用" : state ? calibration[state.calibration_status] : "—");
  setText("reminder", poseSupported === false ? "未启用" : state ? (paused ? "已暂停" : reminders[state.reminder_state]) : "—");
  const frozen = ended ? "（结束）" : pending ? "（暂停）" : "";
  setText("elapsed", poseSupported === false || state?.posture_elapsed_s === "" ? "—" : seconds(state?.posture_elapsed_s, frozen));
  setText("remaining", seconds(state?.release_remaining_s, frozen));
  setText("session", state?.session_id || "—");
  setText("frame", state ? "#" + state.frame_id + " · " + state.timestamp.toFixed(3) + " 秒" : "—");
  setText("age", phase === "offline" || ended ? "—" : seconds(snapshot?.last_frame_age_s));
  setText("quality", evidence ? quality[evidence.quality] : "—");
  setText("presence", evidence ? withScore(presence[evidence.presence], evidence.presence_confidence) : "—");
  setText("target-count", evidence?.target_count >= 0 ? String(evidence.target_count) : "—");
  setText("pose-evidence", poseSupported === false ? "未提供" :
    evidence ? withScore(posture[evidence.posture], evidence.posture_confidence) : "—");

  replaceList("alerts", describeAlerts(phase, state, review, poseSupported, evidence), ([tone, message]) => {
    const item = document.createElement("li");
    item.dataset.tone = tone;
    item.textContent = message;
    return item;
  });
  replaceList("parameters", Object.entries(snapshot?.config ?? {}), ([key, value]) => {
    const row = document.createElement("div");
    const label = document.createElement("dt");
    const reading = document.createElement("dd");
    label.textContent = parameterNames[key] ?? key;
    reading.textContent = value == null ? "未设置" : value + (key.endsWith("_s") ? " 秒" : "");
    row.append(label, reading);
    return row;
  });
  const issues = Array.isArray(snapshot?.known_issues) ? snapshot.known_issues : [];
  element("known-issues-section").hidden = !issues.length;
  replaceList("known-issues", issues, issue => {
    const item = document.createElement("li");
    item.textContent = typeof issue === "string" ? issue : JSON.stringify(issue);
    return item;
  });
  renderEvents(snapshot?.events ?? []);
}

element("event-filter").addEventListener("change", () => renderEvents(shownSnapshot?.events ?? []));

async function refresh() {
  try {
    const result = await fetch("/api/state", {cache: "no-store", signal: AbortSignal.timeout(3000)});
    if (!result.ok) throw new Error("HTTP " + result.status);
    lastSnapshot = await result.json();
    render(lastSnapshot);
  } catch {
    render({...lastSnapshot, phase: "offline"});
  } finally {
    setTimeout(refresh, 400);
  }
}

refresh();
