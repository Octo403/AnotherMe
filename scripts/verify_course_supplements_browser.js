// Use a separate headless browser and the isolated runtime verification server.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const [debugPort='9225',base='http://127.0.0.1:8502',output='.tmp/supplement-browser',paper,pdf,webpage]=process.argv.slice(2);
let socket,next=0;const pending=new Map(),checks={},errors=[];
const course='浏览器补充验证'+Date.now().toString().slice(-6);
function command(method,params={}){return new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timeout'));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.result.description||r.exceptionDetails.text);return r.result.value;}
async function poll(expression){for(let i=0;i<600;i++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,200));}throw Error('Browser wait timeout: '+expression);}
async function screenshot(name){const r=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(r.data,'base64'));}
async function selectFiles(files){const doc=await command('DOM.getDocument'),node=await command('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#course-upload-files'});await command('DOM.setFileInputFiles',{nodeId:node.nodeId,files:files.map(p=>path.resolve(p))});}
function passed(name){checks[name]=true;console.log(name);}
async function main(){
 fs.mkdirSync(output,{recursive:true});let tabs;
 for(let i=0;i<40;i++){try{tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();break;}catch(e){if(i===39)throw e;await new Promise(r=>setTimeout(r,200));}}
 const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith(base));assert(tab);
 socket=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
 socket.onmessage=event=>{const d=JSON.parse(event.data);if(d.id){const t=pending.get(d.id);if(!t)return;clearTimeout(t.timer);pending.delete(d.id);if(d.error)t.reject(Error(d.error.message));else t.resolve(d.result);}else if(d.method==='Runtime.exceptionThrown')errors.push(d.params.exceptionDetails.text);};
 socket.onclose=()=>{for(const t of pending.values()){clearTimeout(t.timer);t.reject(Error('browser closed'));}pending.clear();};
 await command('Runtime.enable');await command('Page.enable');await poll('Boolean(state.bootstrap&&state.history)');
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await evaluate(`openMaterials('course').then(()=>{$('course-name').value=${JSON.stringify(course)};courseInputChanged();closeCoursePicker();return true;})`);
 assert(await evaluate(`$('course-catalog-note').textContent.includes('新建本地课程')`));
 assert(await evaluate(`$('course-supplement-target').textContent.includes(${JSON.stringify(course)})&&$('course-storage-location').textContent.includes(state.bootstrap.question_bank_directory)`));
 passed('target_course_and_actual_local_storage_path_are_visible');
 await selectFiles([paper,pdf]);assert(await evaluate(`$('course-upload-files').files.length===2&&$('course-upload-selection').textContent.includes('2 个文件')`));
 await evaluate(`$('course-supplement-form').scrollIntoView({block:'start'});`);await screenshot('supplement-entry');
 passed('new_course_and_multiple_file_selection_are_visible');
 await evaluate(`$('upload-course-files').click()`);await poll(`!state.busy&&$('course-supplement-status').textContent.includes('已添加 2 份')`);
 assert(await evaluate(`state.bootstrap.question_courses.some(c=>c.name===${JSON.stringify(course)})`));
 await poll(`$('course-progress').textContent.includes('本次处理完成')`);
 assert(await evaluate(`$('course-files').children.length===2&&$('course-progress').textContent.includes('可检索 3 题')`));
 passed('real_form_upload_creates_course_and_finishes_three_questions');
 await evaluate(`$('course-progress').scrollIntoView({block:'start'})`);await screenshot('uploaded-files');
 await selectFiles([paper]);await evaluate(`$('upload-course-files').click()`);await poll(`!state.busy&&$('course-supplement-status').textContent.includes('跳过重复 1 份')`);
 passed('duplicate_file_is_reported_without_replacing_original');
 await evaluate(`$('course-urls').value='file:///C:/secret.pdf';$('add-course-urls').click()`);await poll(`!state.busy&&$('notice').className.includes('error')`);
 assert(await evaluate(`$('course-urls').value==='file:///C:/secret.pdf'`));passed('invalid_url_shows_error_and_preserves_input');
 await selectFiles([webpage]);await evaluate(`$('upload-course-files').click()`);await poll(`!state.busy&&$('course-supplement-status').textContent.includes('已添加 1 份')`);
 await poll(`document.querySelectorAll('.course-attachment').length===1&&$('course-files').innerText.includes('未识别带题号')`);
 assert(await evaluate(`$('course-files').innerText.includes('未识别带题号')`));
 passed('webpage_without_questions_retains_body_and_exposes_attachment_link');
 await evaluate(`document.querySelector('.course-attachment').click()`);await poll(`!state.busy&&$('course-supplement-status').textContent.includes('已添加 1 份')`);
 await poll(`$('course-files').children.length===4&&$('course-progress').textContent.includes('本次处理完成')`);
 assert(await evaluate(`$('course-files').innerText.includes('补充网址')`));passed('webpage_attachment_button_downloads_and_processes_real_public_markdown');
 await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await evaluate(`$('course-supplement-form').scrollIntoView({block:'start'});`);
 assert(await evaluate(`(()=>{const form=$('course-supplement-form'),panel=$('materials'),r=panel.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&form.scrollWidth<=form.clientWidth+1;})()`));
 await screenshot('mobile-supplement');passed('mobile_upload_form_fits_and_panel_scrolls');
 assert.deepEqual(errors,[]);passed('no_browser_javascript_exceptions');
}
main().catch(error=>{errors.push(String(error));process.exitCode=1;}).finally(async()=>{const report={base,course,checks,errors,passed:!errors.length&&Object.values(checks).every(Boolean)};fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));if(socket?.readyState===WebSocket.OPEN){try{await command('Browser.close');}catch{}socket.close();}});
