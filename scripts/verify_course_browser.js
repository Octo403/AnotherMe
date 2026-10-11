// Run against an independently launched headless Edge/Chrome debugging port.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const [debugPort='9224',output='.tmp/course-browser',questionReport]=process.argv.slice(2);
const checks={},errors=[];let socket,nextId=0;const pending=new Map();
function command(method,params={}){
 return new Promise((resolve,reject)=>{const id=++nextId;const timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' 超时'));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});
}
async function evaluate(expression){
 const value=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
 if(value.exceptionDetails)throw Error(value.exceptionDetails.text+': '+value.result?.description);
 return value.result.value;
}
async function screenshot(name){const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));}
async function main(){
 fs.mkdirSync(output,{recursive:true});
 let tabs;
 for(let attempt=0;attempt<30;attempt++){
  try{tabs=await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();break;}
  catch(error){if(attempt===29)throw error;await new Promise(resolve=>setTimeout(resolve,200));}
 }
 const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith('http://127.0.0.1:8501/'));assert(tab,'独立浏览器未打开本地预览');
 socket=new WebSocket(tab.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
 socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.id){const task=pending.get(data.id);if(!task)return;clearTimeout(task.timer);pending.delete(data.id);if(data.error)task.reject(Error(data.error.message));else task.resolve(data.result);}else if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.text);};
 socket.onclose=()=>{for(const task of pending.values()){clearTimeout(task.timer);task.reject(Error('浏览器连接关闭'));}pending.clear();};
 await command('Runtime.enable');await command('Page.enable');
 await evaluate(`new Promise((resolve,reject)=>{let n=0;const poll=()=>{if(state.bootstrap&&state.history)resolve(true);else if(n++>100)reject(Error('页面初始化超时'));else setTimeout(poll,100);};poll();})`);
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await evaluate(`openMaterials('course').then(()=>{openCoursePicker();return true;})`);
 const before=await evaluate(`(()=>{const list=$('available-course-names'),r=list.getBoundingClientRect();return {count:list.querySelectorAll('button').length,total:state.bootstrap.question_courses.length,scrollHeight:list.scrollHeight,height:list.clientHeight,top:list.scrollTop,x:r.x+r.width/2,y:r.y+r.height/2};})()`);
 assert.equal(before.count,before.total);assert(before.count>=54&&before.scrollHeight>before.height&&before.height<=280);
 await command('Input.dispatchMouseEvent',{type:'mouseWheel',x:before.x,y:before.y,deltaX:0,deltaY:500});
 await evaluate('new Promise(resolve=>setTimeout(resolve,250))');
 assert(await evaluate(`$('available-course-names').scrollTop>0`));
 checks.actual_mouse_wheel_scrolls_full_course_list=true;
 await evaluate(`(()=>{const list=$('available-course-names');list.scrollTop=list.scrollHeight;return true;})()`);
 assert(await evaluate(`(()=>{const list=$('available-course-names'),last=list.lastElementChild;return last.getBoundingClientRect().bottom<=list.getBoundingClientRect().bottom+1;})()`));
 await screenshot('courses-last-entry');
 const selected=await evaluate(`(()=>{const last=$('available-course-names').lastElementChild,name=last.firstChild.textContent;last.click();return {name,value:$('course-name').value,closed:$('course-picker-results').hidden};})()`);
 assert.equal(selected.name,selected.value);assert(selected.closed);
 checks.last_course_is_visible_and_selectable=true;
 await evaluate(`$('course-name').value='人工智能';$('course-name').dispatchEvent(new Event('input',{bubbles:true}));`);
 const filtered=await evaluate(`(()=>{const list=$('available-course-names');return [...list.querySelectorAll('button')].map(b=>b.firstChild.textContent);})()`);
 assert(filtered.length>0&&filtered.every(name=>name.includes('人工智能')));
 checks.course_name_filters_real_directory=true;
 await evaluate(`toggleSettings(true)`);
 assert(await evaluate(`!$('settings').hidden&&$('materials').hidden&&!$('question-model-settings').hidden`));
 await screenshot('model-settings');
 await evaluate(`openMaterials('personal')`);
 assert(await evaluate(`$('settings').hidden&&!$('materials').hidden&&!$('personal-library-view').hidden&&$('course-library-view').hidden`));
 checks.model_settings_course_and_personal_material_panels_are_separate=true;
 if(questionReport){
  const report=JSON.parse(fs.readFileSync(questionReport,'utf8'));
  await evaluate(`toggleSettings(false).then(()=>{$('welcome').hidden=true;$('session-title').textContent='Markdown 试题预览';$('turns').replaceChildren(questionCards(${JSON.stringify(report.questions)}));$('conversation').scrollTop=0;return true;})`);
  assert(await evaluate(`document.querySelectorAll('.question-text').length===2&&!$('turns').innerText.includes('当前没有可展示的原题截图')`));
  assert(await evaluate(`document.querySelector('.question-text').innerText.includes('计算给定残差结构')&&document.querySelectorAll('.question-text')[1].innerText.includes('评分标准')`));
  await screenshot('markdown-questions');
  await evaluate(`$('conversation').scrollTop=$('conversation').scrollHeight;`);
  await evaluate(`new Promise(resolve=>setTimeout(resolve,700))`);
  assert(await evaluate(`(()=>{const conversation=$('conversation');return conversation.scrollHeight-conversation.clientHeight-conversation.scrollTop<2;})()`));
  await screenshot('markdown-question-end');
  checks.real_markdown_major_questions_and_subparts_render_without_screenshots=true;
 }
 await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await evaluate(`openMaterials('course').then(()=>{openCoursePicker();return true;})`);
 const mobile=await evaluate(`(()=>{const p=$('materials').getBoundingClientRect(),s=$('materials').querySelector('.panel-scroll');return {left:p.left,right:p.right,width:innerWidth,scrollable:s.scrollHeight>s.clientHeight};})()`);
 assert(mobile.left>=0&&mobile.right<=mobile.width+1&&mobile.scrollable);
 await screenshot('mobile-course-panel');
 checks.narrow_screen_panel_fits_and_scrolls=true;
 assert.deepEqual(errors,[]);checks.no_browser_javascript_exceptions=true;
}
main().catch(error=>{errors.push(String(error));process.exitCode=1;}).finally(async()=>{
 const report={checks,errors,passed:!errors.length&&Object.values(checks).every(Boolean)};
 fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 if(socket?.readyState===WebSocket.OPEN){try{await command('Browser.close');}catch{}socket.close();}
});
