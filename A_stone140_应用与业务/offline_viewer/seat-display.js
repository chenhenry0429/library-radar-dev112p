/* 显示层只读取既有状态；不修改业务阈值、时间线或事件。 */
'use strict';
const extraStyle=document.createElement('style');
extraStyle.textContent=`.seat-overview{display:grid;grid-template-columns:240px 1fr;gap:24px}.seat-tile{border:2px solid #b9ced2;border-radius:14px;padding:20px;text-align:center;background:#f6faf9}.seat-symbol{margin:12px auto;width:80px;height:65px;border:7px solid currentColor;border-radius:15px 15px 6px 6px;border-bottom-width:16px}.seat-symbol:after{content:'';display:block;width:58px;height:18px;margin:49px auto 0;border-left:6px solid currentColor;border-right:6px solid currentColor}.seat-name{font-size:24px;font-weight:600;overflow-wrap:anywhere}.seat-summary{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.seat-summary>span{background:#f1f6f6;padding:8px 14px;border-radius:8px}.alert-item{padding:12px 15px;border-radius:8px;margin:9px 0;background:#fff4db;border-left:4px solid #bb7b0c}.alert-item.danger{background:#fff0ef;border-color:#b44940}.alert-item.info{background:#edf5f8;border-color:#5b869a}.alert-item strong{display:block}.alert-item p{font-size:13px}.scenario-controls{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.scope-note{white-space:pre-line;font-size:13px}.history-list{max-height:180px;overflow:auto}.history-list button{display:block;width:100%;text-align:left;margin:5px 0;padding:8px 12px}.config-note{font:12px/1.7 monospace;overflow-wrap:anywhere;white-space:pre-wrap}body .event-filter{padding:6px 10px}@media(max-width:650px){.seat-overview{grid-template-columns:1fr}.seat-tile{display:flex;gap:20px;align-items:center;text-align:left}.seat-symbol{display:none}}`;
document.head.append(extraStyle);
const seatPanel=document.createElement('section');seatPanel.className='panel';
seatPanel.innerHTML=`<div class="section-head"><h2>座位概览</h2><span class="badge">单座位 · 离线记录</span></div><div class="seat-overview"><div id="seatTile" class="seat-tile"><div><div id="seatName" class="seat-name"></div><div class="seat-symbol" aria-hidden="true"></div></div><div><strong id="seatState"></strong><p id="seatAvailability" class="muted"></p></div></div><div><div class="seat-summary"><span>总座位 <b>1</b></span><span>本时刻可用 <b id="availableCount">0</b></span><span>待确认 / 复核 <b id="pendingCount">0</b></span></div><p id="scopeNote" class="scope-note"></p><p id="recordStatus" class="muted"></p></div></div>`;
$('workspace').insertBefore(seatPanel,$('workspace').querySelector('.cards'));
const anomalyPanel=document.createElement('section');anomalyPanel.className='panel';
anomalyPanel.innerHTML=`<div class="section-head"><h2>异常与能力提示</h2><button id="nextAnomaly">下一异常 →</button></div><div id="activeAlerts" role="status" aria-live="polite"></div><details><summary>已到达的异常记录 <span id="anomalyCount"></span></summary><div id="anomalyHistory" class="history-list"></div></details><details><summary>本次固定参数 / 已知问题</summary><pre id="fixedConfig" class="config-note"></pre><p id="knownIssues" class="scope-note"></p></details>`;
$('workspace').insertBefore(anomalyPanel,seatPanel.nextSibling);
const scenarios=document.createElement('div');scenarios.className='scenario-controls';scenarios.innerHTML=`<span class="muted">验收场景：</span><button id="scenarioNormal">正常占用与提醒</button><button id="scenarioAnomalies">未知 / 复核 / 数据缺口</button><button id="scenarioEof">等待中结束文件</button>`;
$('files').parentElement.parentElement.parentElement.append(scenarios);
const filter=document.createElement('select');filter.id='eventFilter';filter.className='event-filter';filter.setAttribute('aria-label','筛选事件');filter.innerHTML='<option value="all">全部事件</option><option value="anomaly">仅异常</option><option value="reminder">仅提醒</option>';
$('eventCount').parentElement.append(filter);filter.onchange=()=>{if(data)render()};
const anomalyTypes=new Set(['data_gap','review_required']);
function isAnomaly(e){return anomalyTypes.has(e.event_type)}
function anomalyRecords(){
 const records=data.events.filter(isAnomaly).map(e=>({...e,title:eventNames[e.event_type]}));
 data.timeline.forEach((r,i)=>{const previous=data.timeline[i-1];
  if(r.occupancy_pending&&(!previous||!previous.occupancy_pending))records.push({timestamp:r.timestamp,title:'占用待确认',reason:'当前证据不足，保留最近确认状态'});
  if(r.calibration_status==='invalid'&&previous?.calibration_status==='valid')records.push({timestamp:r.timestamp,title:'标定失效',reason:'个人参考失效，停止姿态提醒'});
 });
 return records.sort((a,b)=>a.timestamp-b.timestamp);
}
function viewState(){
 const r=data.timeline[index],next=data.timeline[index+1],cfg=data.run.config||{},gapLimit=cfg.data_gap_s;
 const gap=!!(next&&typeof gapLimit==='number'&&gapLimit>0&&next.timestamp-r.timestamp>gapLimit&&clock-r.timestamp>gapLimit);
 const pending=gap||!!r.occupancy_pending||!!r.review_required||r.occupancy_state==='unknown';
 const available=!pending&&['idle','released'].includes(r.occupancy_state);
 return {r,gap,pending,available,postureSupported:Array.isArray(data.run.capabilities)?data.run.capabilities.includes('posture'):null};
}
function alertsFor(v){
 const {r,gap,postureSupported}=v,a=[];
 if(gap)a.push(['danger','数据缺口：当前无新记录','保留上一帧确认状态，暂停显示计时，不计入可用空位。缺口不等于已确认设备断连。']);
 if(r.review_required)a.push(['danger','需要人工复核','占用证据持续无法确认，暂停自动释放，不计入可用空位。']);
 else if(r.occupancy_pending&&!gap)a.push(['warn','占用待确认','保留最近确认状态；当前不能作为无人证据。具体信号原因需查看 C 端记录。']);
 if(postureSupported===false)a.push(['info','当前版本仅支持占用','坐姿能力未提供；姿态未知不表示异常坐姿，不运行姿态提醒。']);
 else if(r.occupancy_state==='in_use'){
  if(r.calibration_status!=='valid')a.push(['warn',r.calibration_status==='invalid'?'个人标定已失效':'等待有效个人标定','保持占用显示，暂停个人姿态提醒；返回或更换使用者后不能沿用旧参考。']);
  else if(r.posture_state==='unknown')a.push(['warn','姿态暂时未知','占用状态独立保留，姿态累计以输出记录为准，暂停提醒。']);
 }
 if(clock===data.run.end_timestamp)a.push(['info',r.session_id?'文件结束：会话未完成':'文件回放结束',r.session_id?'保留最后会话，不补齐等待时间，不自动释放。':'已到最后一帧；这不是当前设备的实时状态。']);
 return a;
}
const originalRender=render;
render=function(){
 originalRender();const v=viewState(),{r,gap,pending,available,postureSupported}=v;
 text('seatName',r.seat_id);text('seatState',pending?'待确认 · '+label(r.occupancy_state):label(r.occupancy_state));
 text('seatAvailability',available?'该记录时刻可用':pending?'不可计入可用空位':r.occupancy_state==='leave_wait'?'会话保留，等待释放':'当前不可用');
 $('seatTile').style.borderColor=pending?'#bb7b0c':colors[r.occupancy_state];$('seatTile').style.color=pending?'#99620b':'#12655e';
 text('availableCount',available?'1':'0');text('pendingCount',pending?'1':'0');
 const nominal=data.run.time_base?.source==='nominal_frame_period';
 const baseline=String(data.run.result_provenance?.type||data.run.algorithm_version||'').toLowerCase().includes('baseline');
 text('scopeNote',[data.run.source_kind==='measured'?'实测来源':'合成场景',baseline?'离线占用基线':'离线处理结果',postureSupported===false?'仅占用':postureSupported===true?'声明含姿态能力':'旧版记录未声明能力',nominal?'名义时间：不可据此验收实际延迟或丢帧':'按文件时间回放'].join(' · '));
 text('recordStatus','最后记录 '+r.timestamp.toFixed(4)+' 秒；设备实时连接状态：离线文件无法判断。');
 text('fixedConfig',JSON.stringify(data.run.config||{},null,2));text('knownIssues',(data.run.known_issues||[]).join('\n'));
 const alerts=alertsFor(v),container=$('activeAlerts');container.replaceChildren();
 if(!alerts.length){const p=document.createElement('p');p.className='muted';p.textContent='当前记录无待确认、复核或标定告警。';container.append(p)}
 for(const [severity,title,body] of alerts){const box=document.createElement('div');box.className='alert-item '+severity;const strong=document.createElement('strong'),p=document.createElement('p');strong.textContent=title;p.textContent=body;box.append(strong);box.append(p);container.append(box)}
 const records=anomalyRecords(),history=records.filter(e=>e.timestamp<=clock);text('anomalyCount','（'+history.length+'）');const list=$('anomalyHistory');list.replaceChildren();
 history.forEach(e=>{const b=document.createElement('button');b.textContent=e.timestamp.toFixed(4)+' 秒 · '+e.title+' · '+e.reason;b.onclick=()=>{pause();seek(e.timestamp)};list.append(b)});
 $('nextAnomaly').disabled=!records.some(e=>e.timestamp>clock);
 const selected=$('eventFilter').value;[...$('events').children].forEach((tr,i)=>{const e=data.events[i];tr.hidden=selected==='anomaly'?!isAnomaly(e):selected==='reminder'?e.event_type!=='reminder':false});
 if(gap){text('occupancySub','数据缺口，保留最后确认状态');text('posture','未知');text('reminder','已暂停');text('reminderSub','当前时间没有新帧，计时保持最后记录值');text('remaining',r.release_remaining_s===''?'不适用':r.release_remaining_s+' 秒（暂停）');text('noticeTitle','数据缺口');text('noticeBody','当前回放时间缺少记录；不会将空白区间当作无人。')}
 if(postureSupported===false){text('postureSub','此版本不支持坐姿识别');text('reminder','未启用');text('reminderSub','当前算法仅提供占用结果');text('calibration','不适用')}
};
$('nextAnomaly').onclick=()=>{pause();const e=anomalyRecords().find(e=>e.timestamp>clock);if(e)seek(e.timestamp)};
$('scenarioNormal').onclick=()=>$('demo').onclick();
for(const [id,key] of [['scenarioAnomalies','anomalies'],['scenarioEof','eof_wait']])$(id).onclick=()=>{try{load(window.ACCEPTANCE_DATA[key])}catch(e){fail(e)}};
const originalFail=fail;fail=function(e){originalFail(e);if(data)text('error',(e.message||String(e))+'\n导入失败，下面仍显示上一份有效记录：'+data.run.recording_id)};
