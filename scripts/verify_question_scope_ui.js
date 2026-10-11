// Exercise the real @ handlers; browser layout needs separate visual inspection.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const controls=new Map();let focused=null,lastAction=null;
function node(tag,text=''){
 return {tagName:tag, textContent:text,value:'',hidden:false,disabled:false,children:[],attributes:{},selectionStart:0,
  setAttribute(name,value){this.attributes[name]=value;},
  replaceChildren(...items){this.children=[...items];},append(...items){this.children.push(...items);},
  querySelector(tag){return this.children.find(child=>child.tagName===tag)||null;},
  focus(){focused=this;},setSelectionRange(start){this.selectionStart=start;},
  click(){lastAction=this.onclick?.();return lastAction;}
 };
}
const html=fs.readFileSync('src/aitown/ui/static/index.html','utf8');
for(const match of html.matchAll(/id="([^"]+)"/g))controls.set(match[1],node('div'));
const sessions={one:{session_id:'one',question_course:'',scope:'synthetic'},two:{session_id:'two',question_course:'',scope:''}};
const state={busy:false,session:'one',history:{session:{...sessions.one}},bootstrap:{question_courses:[{id:'fdu-set-graph-theory',name:'集合与图论'}]}};
let failNext=false,sent=0;
const requests=[];
const sandbox={state,console,$:id=>{assert(controls.has(id),id);return controls.get(id);},el:node,
 setBusy:busy=>state.busy=busy,notice:()=>{},refreshBootstrap:async()=>{},send:()=>sent++,
 refreshHistory:async()=>{state.history={session:{...sessions[state.session]}};controls.get('current-scope').textContent='仅查询知识资料';sandbox.renderQuestionScope();},
 api:async(path,payload)=>{
  requests.push({path,payload});assert.equal(path,'/api/question-scope');
  if(failNext){failNext=false;throw Error('模拟保存失败');}
  sessions[payload.session].question_course=payload.course;return {...sessions[payload.session]};
 }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('src/aitown/ui/static/question_scope.js','utf8'),sandbox);
const app=fs.readFileSync('src/aitown/ui/static/app.js','utf8');
vm.runInContext(app.split('\n').find(line=>line.startsWith("$('send').onclick=send;")),sandbox);
function type(value,cursor=value.length){const message=controls.get('message');message.value=value;message.selectionStart=cursor;message.oninput();}
function key(name,composing=false){let prevented=false;controls.get('message').onkeydown({key:name,isComposing:composing,preventDefault(){prevented=true;}});return prevented;}
async function main(){
 const checks={};sandbox.renderQuestionScope();
 type('@');assert.equal(controls.get('question-course-options').hidden,false);
 assert.equal(controls.get('question-course-options').children[0].textContent,'@集合与图论');
 assert(key('Enter'));await lastAction;
 assert.equal(sent,0);assert.equal(controls.get('message').value,'');
 assert.equal(controls.get('pinned-question-course').hidden,false);
 assert.equal(sessions.one.question_course,'fdu-set-graph-theory');
 checks.at_menu_enter_selects_and_persists_without_sending=true;
 type('找题 @集 排列组合',5);await controls.get('question-course-options').children[0].click();
 assert.equal(controls.get('message').value,'找题  排列组合');
 assert.equal(controls.get('message').selectionStart,3);
 checks.mention_removal_preserves_remaining_draft=true;
 controls.get('choose-question-course').click();
 assert.equal(controls.get('question-course-options').hidden,false);
 assert(key('Escape'));assert.equal(controls.get('question-course-options').hidden,true);
 type('@');assert(!key('Enter',true));assert.equal(sessions.one.question_course,'fdu-set-graph-theory');
 checks.escape_and_chinese_composition_do_not_send_or_change_scope=true;
 state.session='two';await sandbox.refreshHistory();
 assert.equal(controls.get('pinned-question-course').hidden,true);
 state.session='one';await sandbox.refreshHistory();
 assert.equal(controls.get('pinned-question-course').hidden,false);
 checks.switching_sessions_restores_each_scope=true;
 controls.get('message').value='保留草稿';await controls.get('clear-question-course').click();
 assert.equal(sessions.one.question_course,'');assert.equal(controls.get('pinned-question-course').hidden,true);
 assert.equal(controls.get('message').value,'保留草稿');
 checks.cancel_restores_auto_scope_and_keeps_draft=true;
 failNext=true;type('@集');await controls.get('question-course-options').children[0].click();
 assert.equal(state.busy,false);assert.equal(sessions.one.question_course,'');assert.equal(controls.get('message').value,'@集');
 checks.failure_retains_draft_and_does_not_pin=true;
 assert(requests.every(request=>request.payload.session==='one'));
 console.log(JSON.stringify({checks,passed:true,note:'真实 JS 事件与 DOM fixture；未验证浏览器布局。'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
