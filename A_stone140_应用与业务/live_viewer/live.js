"use strict";

const element = id => document.getElementById(id);
const setText = (id, text) => { element(id).textContent = text; };
const phases = {waiting: "等待首帧", receiving: "接收中", interrupted: "数据中断", ended: "回放结束", offline: "服务断开"};
const occupancy = {unknown: "未知", idle: "空闲", in_use: "使用中", leave_wait: "离座等待", released: "已释放"};
const posture = {unknown: "未知", not_applicable: "不适用", reference: "参考范围", forward: "前倾", backward: "后靠"};
const reminders = {idle: "未计时", accumulating: "计时中", reminded: "已提醒", cooldown: "冷却中", paused: "已暂停"};
const events = {occupancy_change: "占用变化", posture_change: "姿态变化", calibration_required: "需要标定",
  data_gap: "数据缺口", review_required: "人工复核", reminder: "姿态提醒"};
let lastSnapshot = null;
let renderedEvents = "";

function render(snapshot) {
  const state = snapshot?.state;
  const phase = snapshot?.phase ?? "offline";
  const active = phase === "receiving";
  const ended = phase === "ended";
  const pending = !active || !state || state.occupancy_pending || state.review_required;
  const review = snapshot?.silence_review || state?.review_required;
  const available = active && snapshot.available;
  const poseSupported = snapshot?.capabilities?.includes("posture");
  setText("connection", phases[phase]);
  if (snapshot?.mode) {
    const origin = snapshot.source_kind === "synthetic" ? "合成数据" : "实测数据";
    const mode = snapshot.mode === "replay" ? "逐帧回放" : "在线接收";
    const timebase = snapshot.nominal_time ? " · 名义时间" : "";
    setText("source", `${mode} · ${origin} · ${snapshot.algorithm_version}${timebase}`);
  }
  setText("seat", snapshot?.seat_id ? `座位 ${snapshot.seat_id}` : "座位");
  setText("occupancy", state ? occupancy[state.occupancy_state] : "未知");
  setText("availability", ended ? "回放结束，保留最后记录" : review ? "需人工复核 · 不可用" :
    pending ? "待确认 · 不可用" : available ? "可用" : "不可用");
  element("seatPanel").dataset.tone = ended ? "history" : pending || state?.occupancy_state === "leave_wait" ? "attention" : "ready";
  setText("posture", poseSupported === false ? "未提供" : state ? (pending && !ended ? "未知" : posture[state.posture_state]) : "—");
  setText("reminder", poseSupported === false ? "未启用" : state ? (pending && !ended ? "已暂停" : reminders[state.reminder_state]) : "—");
  const seconds = state?.release_remaining_s;
  setText("remaining", typeof seconds === "number" ? `${seconds.toFixed(1)} 秒${!active ? "（停止）" : pending ? "（暂停）" : ""}` : "—");
  setText("frame", state ? `#${state.frame_id} · ${state.timestamp.toFixed(3)} 秒` : "—");
  const recent = (snapshot?.events ?? []).slice(-12).reverse();
  const key = JSON.stringify(recent);
  if (key !== renderedEvents) {
    renderedEvents = key;
    element("eventEmpty").hidden = !!recent.length;
    element("events").replaceChildren(...recent.map(event => {
      const item = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = `${event.timestamp.toFixed(3)} 秒`;
      item.append(time, `${events[event.event_type] ?? event.event_type} · ${event.reason}`);
      return item;
    }));
  }
}

async function refresh() {
  try {
    const result = await fetch("/api/state", {cache: "no-store", signal: AbortSignal.timeout(3000)});
    if (!result.ok) throw new Error(`HTTP ${result.status}`);
    lastSnapshot = await result.json();
    render(lastSnapshot);
  } catch {
    render({...lastSnapshot, phase: "offline"});
  } finally {
    setTimeout(refresh, 400);
  }
}

refresh();
