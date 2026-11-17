// 在轻量 DOM 中检查显示逻辑，结果不冒充真实浏览器视觉验收。
require('./test-viewer.cjs');
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
class E{constructor(){this.children=[];this.style={};this.value='all';this.classList={add(){},remove(){}}}append(x){this.children.push(x)}replaceChildren(){this.children=[]}get parentElement(){return this}querySelector(){return null}insertBefore(x){this.children.push(x)}setAttribute(){}}
const map=new Map(),get=id=>{if(!map.has(id))map.set(id,new E());return map.get(id)};
const ctx=vm.createContext({structuredClone,console,window:{},document:{head:new E(),createElement:()=>new E(),getElementById:get},performance:{now:()=>0},requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
const html=fs.readFileSync(__dirname+'/index.html','utf8');
for(const f of ['demo-data.js','acceptance-data.js'])vm.runInContext(fs.readFileSync(__dirname+'/'+f,'utf8'),ctx);
vm.runInContext(html.match(/<script>\s*([\s\S]*?)<\/script>/)[1],ctx);
vm.runInContext(fs.readFileSync(__dirname+'/seat-display.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx),results=[];
function check(id,scenario,expected,fn){fn();results.push({id,scenario,expected,status:'通过',method:'Node.js + 轻量 DOM 断言'})}
function alerts(){return run('alertsFor(viewState()).map(a=>a[1]).join("；")')}
run('load(window.DEMO_DATA)');
check('A01','初始未知','可用为 0，待确认为 1',()=>{assert.equal(get('availableCount').textContent,'0');assert.equal(get('pendingCount').textContent,'1')});
run('seek(3)');check('A02','确认空闲','可用为 1',()=>assert.equal(get('availableCount').textContent,'1'));
run('seek(7)');check('A03','已入座','可用为 0，显示座位与未标定提示',()=>{assert.equal(get('availableCount').textContent,'0');assert.equal(get('seatName').textContent,'seat_01');assert.match(alerts(),/标定/)});
run('seek(33)');check('A04','持续姿态','输出累计 10 秒且只展示已有提醒',()=>{assert.equal(get('reminder').textContent,'已提醒');assert.equal(get('elapsed').textContent,'10.0 秒')});
run('seek(45)');check('A05','离座等待','等待剩余 60 秒，不计可用',()=>{assert.equal(get('remaining').textContent,'60.0 秒');assert.equal(get('availableCount').textContent,'0')});
run('seek(51)');check('A06','等待中返回','保持使用中，重新标定提示',()=>{assert.equal(get('occupancy').textContent,'使用中');assert.match(alerts(),/标定/)});
run('seek(128)');check('A07','确认释放','可用为 1、无活动会话',()=>{assert.equal(get('availableCount').textContent,'1');assert.equal(get('session').textContent,'无活动会话')});
run('load(window.ACCEPTANCE_DATA.anomalies);seek(16)');check('A08','低置信证据','保留使用中、待确认、不可用',()=>{assert.equal(get('occupancy').textContent,'使用中');assert.equal(get('pendingCount').textContent,'1');assert.equal(get('availableCount').textContent,'0')});
run('seek(55)');check('A09','持续未知','人工复核提示，不释放',()=>{assert.match(alerts(),/人工复核/);assert.equal(get('occupancy').textContent,'使用中')});
run('seek(65)');check('A10','恢复确认','清除当前复核提示、保留异常历史',()=>{assert.doesNotMatch(alerts(),/人工复核/);assert.ok(get('anomalyHistory').children.length>0)});
run('seek(75)');check('A11','两帧之间的数据缺口','显示缺口、暂停、不假定断连',()=>{assert.equal(run('viewState().gap'),true);assert.equal(get('reminder').textContent,'未启用');assert.equal(get('availableCount').textContent,'0');assert.match(get('recordStatus').textContent,/无法判断/)});
run('seek(80)');check('A12','缺口后的记录','回放时间 80 秒，缺口事件进入历史',()=>{assert.equal(run('clock'),80);assert.ok(run('anomalyRecords().some(e=>e.timestamp===80&&e.event_type==="data_gap")'))});
run('seek(0)');get('nextAnomaly').onclick();check('A13','下一异常导航','跳到 15 秒占用待确认',()=>assert.equal(run('clock'),15));
get('eventFilter').value='anomaly';run('render()');check('A14','事件筛选','仅显示事件文件中的缺口或复核',()=>{assert.ok(run('[...$("events").children].every((tr,i)=>tr.hidden===!isAnomaly(data.events[i]))'))});
get('eventFilter').value='all';run('load(window.ACCEPTANCE_DATA.eof_wait);seek(20)');check('A15','等待中 EOF','保留等待及剩余 51 秒、不补齐释放',()=>{assert.equal(get('occupancy').textContent,'离座等待');assert.equal(get('remaining').textContent,'51.0 秒');assert.match(alerts(),/会话未完成/);assert.equal(run('playing'),false)});
const real=path.resolve(__dirname,'fixtures/v02');
ctx.real={timeline:fs.readFileSync(real+'/timeline.csv','utf8'),events:fs.readFileSync(real+'/events.csv','utf8'),run:fs.readFileSync(real+'/run.json','utf8')};
run('load({timeline:csv(real.timeline,timelineFields,"timeline"),events:csv(real.events,eventFields,"events"),run:JSON.parse(real.run)});seek(22)');check('A16','实测基线 v0.2','仅占用、姿态未启用、名义时间明确',()=>{assert.match(get('scopeNote').textContent,/离线占用基线.*仅占用.*名义时间/);assert.equal(get('reminder').textContent,'未启用');assert.match(alerts(),/仅支持占用/)});
run('seek(162.3465)');check('A17','实测基线 EOF','保持使用中且不可用',()=>{assert.equal(get('availableCount').textContent,'0');assert.match(alerts(),/会话未完成/)});
check('A18','错误文件导入','拒绝错误包、保持旧记录并提示',()=>{const previous=run('data.run.recording_id');assert.throws(()=>run('load({timeline:[],events:[],run:{}})'));run('fail(new Error("字段缺失"))');assert.match(get('error').textContent,/上一份有效记录/);assert.equal(run('data.run.recording_id'),previous)});
const out=path.resolve(__dirname,'test-results');fs.mkdirSync(out,{recursive:true});fs.writeFileSync(out+'/页面逐项验收.json',JSON.stringify({test_type:'display_logic_not_browser_visual',results},null,2));console.log(results.length+' 项页面显示逻辑验收通过。');
