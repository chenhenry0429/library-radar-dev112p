// 无第三方依赖的页面逻辑检查；不替代浏览器视觉检查。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.style={};this.value='10';this.classList={add(){},remove(){}}}
 append(child){this.children.push(child)}
 replaceChildren(){this.children=[]}
 get parentElement(){return this}
 querySelector(){return null}
 insertBefore(child){this.children.push(child)}
 setAttribute(){}
}
const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const context=vm.createContext({console,structuredClone,window:{},document:{head:new Element(),getElementById:get,createElement:()=>new Element()},performance:{now:()=>0},requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{}});
const html=fs.readFileSync(__dirname+'/index.html','utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
vm.runInContext(fs.readFileSync(__dirname+'/demo-data.js','utf8'),context);
vm.runInContext(script,context);
vm.runInContext(fs.readFileSync(__dirname+'/acceptance-data.js','utf8'),context);
vm.runInContext(fs.readFileSync(__dirname+'/seat-display.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
run('load(window.DEMO_DATA)');
assert.equal(get('occupancy').textContent,'未知');
run('seek(33)');assert.equal(get('reminder').textContent,'已提醒');assert.equal(get('elapsed').textContent,'10.0 秒');
run('seek(51)');assert.equal(get('occupancy').textContent,'使用中');assert.equal(get('calibration').textContent,'未标定');
run('seek(128)');assert.equal(get('occupancy').textContent,'已释放');assert.equal(get('session').textContent,'无活动会话');
run('seek(150)');assert.equal(run('playing'),false);assert.equal(get('next').disabled,true);
run('seek(0)');get('next').onclick();assert.equal(run('clock'),3);
const t=run('csv("\\uFEFFa,b\\r\\n1,\\\"两字,逗号\\\"\\r\\n",["a","b"],"test")');assert.equal(t[0].b,'两字,逗号');
assert.throws(()=>run('csv("a,b\\n1,\\\"未闭合",["a","b"],"test")'));
assert.throws(()=>run('const bad=structuredClone(window.DEMO_DATA);bad.run.frame_count=999;validate(bad)'));
// 用真实 CSV 导入路径验证 BOM、空字段、事件关联与数量检查。
const path=require('node:path');
const one=path.resolve(__dirname,'fixtures/v02');
context.importedText={timeline:fs.readFileSync(one+'/timeline.csv','utf8'),events:fs.readFileSync(one+'/events.csv','utf8'),run:fs.readFileSync(one+'/run.json','utf8')};
run('load({timeline:csv(importedText.timeline,timelineFields,"timeline.csv"),events:csv(importedText.events,eventFields,"events.csv"),run:JSON.parse(importedText.run)})');
assert.equal(get('events').children.length,3);
run('const legacy=structuredClone(window.DEMO_DATA);delete legacy.run.frame_count;delete legacy.run.event_count;load(legacy)');
assert.equal(get('events').children.length,11);
console.log('通过：模拟载入、真实 CSV 解析、状态跳转、提醒计时、EOF、事件导航、错误文件拒绝。');
