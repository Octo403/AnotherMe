// Real event handlers with select semantics; this does not verify browser layout.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const controls=new Map();
const html=fs.readFileSync('src/aitown/ui/static/index.html','utf8');
function control(tag,options=[]){
 let current=options[0]?.value||'';
 return {tagName:tag.toUpperCase(),checked:false,disabled:false,hidden:false,textContent:'',options,
  get value(){return current;},
  set value(value){current=tag==='select'&&!this.options.some(item=>item.value===String(value))?'':String(value);},
  replaceChildren(...items){this.options=[...items];current=items[0]?.value||'';},
  append(item){this.options.push(item);if(this.options.length===1)current=item.value;}
 };
}
for(const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)){
 const options=[];
 if(match[1]==='select'){
  const body=html.slice(match.index+match[0].length).split('</select>')[0];
  for(const item of body.matchAll(/<option\b[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g))options.push({value:item[1],textContent:item[2]});
 }
 controls.set(match[2],control(match[1],options));
}
const saved={search_mode:'none',api:{provider:'deepseek',base_url:'https://api.deepseek.com',model:'',
 temperature:.2,max_tokens:4096,response_format:'json_object',images:false,has_key:false},
 codex_model:'',codex_images:true,extractor:'basic',mineru_model:'vlm',mineru_has_token:false,
 remember_keys:false,data_scope:'仅题库与找题消息',sample_sources:[]};
const state={busy:false,bootstrap:{question_model:'none'}};
const requests=[];
let currentJob,failNext=false;
const sandbox={state,console,setTimeout,
 $:id=>{assert(controls.has(id),'Missing HTML control: '+id);return controls.get(id);},
 el:(tag,text='')=>({value:'',textContent:text}),setBusy:value=>{state.busy=value;},notice:()=>{},
 refreshBootstrap:async()=>{},
 localStorage:{setItem(){throw Error('Secrets must not enter browser storage');}},
 api:async(path,payload)=>{
  if(path==='/api/job?id=fixture')return currentJob;
  if(path==='/api/model-settings'&&!payload)return structuredClone(saved);
  requests.push({path,payload});
  if(failNext){currentJob={status:'failed',error:'模拟连接失败'};failNext=false;}
  else if(path==='/api/model-settings'){
   Object.assign(saved,payload,{api:{...payload.api,has_key:!!payload.api.api_key},mineru_has_token:!!payload.mineru_token});
   delete saved.api.api_key;delete saved.mineru_token;
   currentJob={status:'complete',result:structuredClone(saved)};
  }else if(path==='/api/model-catalog')currentJob={status:'complete',result:payload.search_mode==='codex_chatgpt'?
   {models:['account-default','account-alternative'],choices:[{id:'account-default',name:'Default model',is_default:true},{id:'account-alternative',name:'Alternative model'}]}:
   {models:['exact-model','other-model']}};
  else currentJob={status:'complete',result:{message:'合成测试通过'}};
  return {job_id:'fixture'};
 }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('src/aitown/ui/static/models.js','utf8'),sandbox);
async function main(){
 const checks={};
 assert.equal(controls.get('model-id').tagName,'SELECT');assert.equal(controls.get('codex-model-id').tagName,'SELECT');
 assert(!controls.has('model-catalog'));
 sandbox.renderModelSettings(saved);
 controls.get('question-model-mode').value='api';await controls.get('question-model-mode').onchange();
 assert.equal(controls.get('api-model-fields').hidden,false);
 controls.get('model-api-key').value='synthetic-key';
 await controls.get('load-models').onclick();
 assert.equal(controls.get('model-id').options[1].value,'exact-model');
 controls.get('model-id').value='invented-model';assert.equal(controls.get('model-id').value,'');
 controls.get('model-id').value='exact-model';
 assert.equal(controls.get('model-api-key').value,'synthetic-key');
 checks.api_select_uses_catalog_and_retains_key=true;
 controls.get('mineru-token').value='synthetic-token';
 await controls.get('save-model-settings').onclick();
 const save=requests.find(item=>item.path==='/api/model-settings');
 assert.equal(save.payload.api.model,'exact-model');
 assert.equal(controls.get('model-id').value,'exact-model');
 assert.equal(controls.get('model-api-key').value,'');assert.equal(controls.get('mineru-token').value,'');
 assert(!controls.get('model-settings-status').textContent.includes('synthetic-key'));
 checks.save_selection_and_secret_clear=true;
 controls.get('model-provider').value='openai';controls.get('model-provider').onchange();
 assert.equal(controls.get('model-base-url').value,'https://api.openai.com/v1');
 assert.equal(controls.get('model-id').value,'');assert.equal(controls.get('model-api-key').value,'');
 assert.equal(controls.get('model-id').options.length,1);
 checks.provider_change_clears_catalog_and_key=true;
 controls.get('question-model-mode').value='codex_chatgpt';await controls.get('question-model-mode').onchange();
 const codexRead=requests.find(item=>item.path==='/api/model-catalog'&&item.payload.search_mode==='codex_chatgpt');
 assert.deepEqual(Object.keys(codexRead.payload),['search_mode','codex_path']);
 assert.equal(controls.get('codex-model-fields').hidden,false);
 assert.equal(controls.get('codex-model-id').value,'account-default');
 assert(controls.get('codex-model-id').options[1].textContent.includes('默认推荐'));
 controls.get('codex-model-id').value='account-alternative';await controls.get('save-model-settings').onclick();
 assert.equal(saved.codex_model,'account-alternative');
 await sandbox.refreshModelSettings();assert.equal(controls.get('codex-model-id').value,'account-alternative');
 checks.codex_auto_load_default_and_persist_exact_selection=true;
 controls.get('codex-cli-path').value='C:\\Custom Tools\\codex.cmd';controls.get('codex-cli-path').onchange();
 await controls.get('load-codex-models').onclick();
 assert.equal(requests.at(-1).payload.codex_path,'C:\\Custom Tools\\codex.cmd');
 assert(!('api' in requests.at(-1).payload));
 await controls.get('save-model-settings').onclick();await sandbox.refreshModelSettings();
 assert.equal(saved.codex_path,'C:\\Custom Tools\\codex.cmd');
 assert.equal(controls.get('codex-cli-path').value,saved.codex_path);
 checks.manual_codex_path_load_save_and_reload=true;
 failNext=true;await controls.get('load-codex-models').onclick();
 assert.equal(state.busy,false);assert.equal(controls.get('codex-model-id').value,'account-alternative');
 assert(controls.get('codex-catalog-status').textContent.includes('模拟连接失败'));
 checks.catalog_failure_retains_selection_and_allows_retry=true;
 await controls.get('load-codex-models').onclick();
 assert.equal(controls.get('codex-model-id').value,'account-alternative');
 sandbox.renderModelSettings({...saved,codex_model:'previous-model'});
 assert.equal(controls.get('codex-model-id').value,'previous-model');
 assert(controls.get('codex-model-id').options.at(-1).textContent.includes('当前目录未返回'));
 checks.missing_saved_model_is_visible_without_silent_substitution=true;
 failNext=true;controls.get('model-api-key').value='retry-key';
 await controls.get('test-model').onclick();
 assert.equal(state.busy,false);assert.equal(controls.get('model-api-key').value,'retry-key');
 assert.equal(controls.get('model-settings-status').textContent,'模拟连接失败');
 checks.failure_unblocks_and_retains_unsaved_key=true;
 controls.get('question-model-mode').value='api';controls.get('model-manual-id').value='manual-provider-model';
 await controls.get('save-model-settings').onclick();
 assert.equal(saved.api.model,'manual-provider-model');
 assert.equal(controls.get('model-manual-id').value,'');
 checks.manual_model_works_without_catalog=true;
 console.log(JSON.stringify({checks,passed:true,note:'真实 JS 事件 + DOM fixture；未进行浏览器视觉验收或线上模型推理'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
