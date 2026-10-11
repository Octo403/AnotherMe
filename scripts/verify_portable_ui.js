// Headless Edge against an actual extracted portable runtime.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const [debugPort,base,output]=process.argv.slice(2);
let socket,next=0;const pending=new Map(),checks={},errors=[];
function command(method,params={}){return new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timeout'));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.result.description||r.exceptionDetails.text);return r.result.value;}
async function poll(expression){for(let i=0;i<300;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,200));}throw Error('Timed out: '+expression);}
async function screenshot(name){const r=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(r.data,'base64'));}
async function main(){
 fs.mkdirSync(output,{recursive:true});
 const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith(base));assert(tab);
 socket=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
 socket.onmessage=event=>{const d=JSON.parse(event.data);if(d.id){const t=pending.get(d.id);if(!t)return;clearTimeout(t.timer);pending.delete(d.id);if(d.error)t.reject(Error(d.error.message));else t.resolve(d.result);}else if(d.method==='Runtime.exceptionThrown')errors.push(d.params.exceptionDetails.exception?.description||d.params.exceptionDetails.text);};
 await command('Runtime.enable');await command('Page.enable');await poll("typeof state!=='undefined'&&Boolean(state.bootstrap&&state.history)");
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await poll(`!$('first-run-setup').hidden&&state.panel==='settings'`);
 assert(await evaluate(`$('question-model-mode').value==='api'&&$('remember-model-keys').checked&&$('setup-document-status').textContent.includes('已就绪')`));
 checks.first_run_automatically_opens_api_guide_and_detects_bundled_converter=true;
 assert(await evaluate(`$('setup-storage').textContent.includes(state.bootstrap.question_bank_directory)`));
 checks.real_persistent_storage_location_is_visible=true;await screenshot('first-run-desktop');
 await evaluate(`$('model-provider').value='local';$('model-provider').onchange();$('model-base-url').value='http://127.0.0.1:8765/v1';$('model-api-key').value='local-only';$('model-manual-id').value='qwen3-4b-local';$('finish-setup').click()`);
 await poll(`!state.busy&&state.bootstrap.question_model==='api'&&$('first-run-setup').hidden&&state.panel==='materials'`);
 assert(await evaluate(`!$('course-library-view').hidden&&!$('model-api-key').value`));
 checks.real_api_test_save_and_finish_opens_materials_and_clears_key=true;await screenshot('configured-materials');
 await evaluate(`$('settings-button').onclick().then(()=>true)`);await poll(`!state.busy&&state.panel==='settings'&&state.modelSettings.api.has_key`);
 assert(await evaluate(`$('model-id').value==='qwen3-4b-local'&&!$('model-manual-id').value`));
 checks.manual_model_and_encrypted_key_are_reflected_in_settings=true;
 await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await evaluate(`$('reopen-setup').click()`);await poll(`!$('first-run-setup').hidden`);
 assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`));await screenshot('first-run-mobile');
 checks.mobile_guide_has_no_horizontal_overflow=true;
 await evaluate(`$('skip-setup').click()`);await poll(`!state.busy&&state.bootstrap.question_model==='none'&&state.panel==='materials'`);
 checks.skip_explicitly_switches_to_local_search=true;
 await command('Page.reload');await new Promise(r=>setTimeout(r,500));await poll(`typeof state!=='undefined'&&state.bootstrap&&!state.bootstrap.setup_required&&state.history`);
 assert(await evaluate(`$('first-run-setup').hidden`));checks.guide_completion_survives_reload=true;
 assert.equal(errors.length,0);checks.no_browser_javascript_errors=true;
 fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({checks,passed:true,errors,note:'真实解压包 + Edge + 本机 Qwen API，桌面与 390px 布局；非其他设备验证'},null,2));console.log(JSON.stringify({checks,passed:true},null,2));
}
main().catch(error=>{fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({checks,passed:false,error:String(error),errors},null,2));console.error(error);process.exitCode=1;}).finally(()=>{socket?.close();for(const t of pending.values())clearTimeout(t.timer);});
