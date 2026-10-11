// Secrets stay in input memory until POST and are cleared after successful save.
const modelTemplates={deepseek:'https://api.deepseek.com',openai:'https://api.openai.com/v1',local:'http://127.0.0.1:8000/v1',custom:''};
const modelCatalogs={api:null,codex_chatgpt:null};
function apiCatalogKey(){return $('model-provider').value+'|'+$('model-base-url').value.trim().replace(/\/$/,'');}
function modelOptions(mode,selected=''){
 const select=$(mode==='api'?'model-id':'codex-model-id');
 const catalog=modelCatalogs[mode];
 const choices=catalog&&(mode!=='api'||catalog.key===apiCatalogKey())?catalog.choices:[];
 select.replaceChildren();
 const placeholder=el('option',choices.length?'请选择模型型号':mode==='api'?'填写 Key 后读取型号列表':'请刷新型号列表');
 placeholder.value='';select.append(placeholder);
 for(const item of choices){
  const label=(item.name&&item.name!==item.id?`${item.name} · ${item.id}`:item.id)+(item.is_default?'（默认推荐）':'');
  const option=el('option',label);option.value=item.id;select.append(option);
 }
 if(selected&&!choices.some(item=>item.id===selected)){
  const option=el('option',selected+(catalog?'（此前选择，当前目录未返回）':'（此前选择，待刷新目录）'));
  option.value=selected;select.append(option);
 }
 select.value=selected||(mode==='codex_chatgpt'?choices.find(item=>item.is_default)?.id||choices[0]?.id||'':'');
}
async function loadModelCatalog(mode){
 // Reading the CLI directory needs neither API settings nor other secrets.
 const form=mode==='api'?modelSettingsPayload():null;
 const payload=mode==='api'?{search_mode:mode,api:form.api,clear_api_key:form.clear_api_key}:{search_mode:mode};
 return modelSettingsJob('/api/model-catalog',payload);
}
function renderQuestionModelBadge(){
 const data=state.bootstrap;if(!data)return;
 const label=data.question_model==='api'?`题库 API · ${data.question_model_name}`:data.question_model==='codex_chatgpt'?'题库 · ChatGPT 第二轮':'题库 · 本地筛选';
 $('mode-badge').textContent=label;
 $('course-search-mode').textContent=data.question_model==='codex_chatgpt'?'找题流程：本地召回 → ChatGPT 根据本次提问筛选，也支持参考题追问。':data.question_model==='api'?'当前使用 API 筛题；可在模型设置切换到 ChatGPT 登录。':'当前仅本地筛选；要启用第二轮 ChatGPT，请在“筛题模型设置”中选择 ChatGPT 登录并保存。';
 if(data.question_model==='api')$('composer-note').textContent='找题消息交给所选 API 模型理解与筛选；个人资料仍只在本机处理。Enter 发送 · Shift + Enter 换行';
}
function modelSettingsPayload(){return {
 search_mode:$('question-model-mode').value,
 api:{provider:$('model-provider').value,base_url:$('model-base-url').value.trim(),model:$('model-manual-id').value.trim()||$('model-id').value.trim(),
  api_key:$('model-api-key').value.trim(),temperature:Number($('model-temperature').value),max_tokens:Number($('model-max-tokens').value),
  response_format:$('model-response-format').value,images:$('model-images').checked},
 codex_model:$('codex-model-id').value.trim(),codex_images:$('codex-model-images').checked,
 extractor:$('pdf-extractor').value,mineru_model:$('mineru-model').value,mineru_token:$('mineru-token').value.trim(),
 remember_keys:$('remember-model-keys').checked,clear_api_key:$('clear-api-key').checked,clear_mineru_token:$('clear-mineru-token').checked
};}
function modelFieldVisibility(){
 $('api-model-fields').hidden=$('question-model-mode').value!=='api';
 $('codex-model-fields').hidden=$('question-model-mode').value!=='codex_chatgpt';
}
function renderModelSettings(data){
 state.modelSettings=data;
 $('question-model-mode').value=data.search_mode;
 $('model-provider').value=data.api.provider;
 $('model-base-url').value=data.api.base_url;modelOptions('api',data.api.model);
 $('model-manual-id').value='';
 $('model-temperature').value=data.api.temperature;$('model-max-tokens').value=data.api.max_tokens;
 $('model-response-format').value=data.api.response_format;$('model-images').checked=data.api.images;
 $('model-api-key').value='';$('mineru-token').value='';
 $('clear-api-key').checked=false;$('clear-mineru-token').checked=false;
 $('model-key-state').textContent=data.api.has_key?'已有 Key，留空保留；更换服务商或地址需重新填写。':'未设置 Key';
 $('mineru-token-state').textContent=data.mineru_has_token?'已有 Token，留空保留。':'未设置 Token';
 modelOptions('codex_chatgpt',data.codex_model);$('codex-model-images').checked=data.codex_images;
 $('pdf-extractor').value=data.extractor;$('mineru-model').value=data.mineru_model;
 $('remember-model-keys').checked=data.remember_keys;
 const sources=$('mineru-sample-source');sources.replaceChildren();
 for(const source of data.sample_sources||[]){const option=el('option',source.name);option.value=source.id;sources.append(option);}
 $('mineru-sample').disabled=state.busy||!sources.options.length;
 $('model-settings-status').textContent=data.warning||`当前找题：${data.search_mode==='api'?data.api.provider+' / '+data.api.model:data.search_mode==='codex_chatgpt'?'ChatGPT 第二轮筛选':'本地筛选，无模型调用'}。${data.data_scope}。`;
 modelFieldVisibility();
 if(typeof renderSetupDetails==='function')renderSetupDetails(data);
}
async function refreshModelSettings(){
 renderModelSettings(await api('/api/model-settings'));
 const mode=$('question-model-mode').value;
 if(mode==='codex_chatgpt'&&!modelCatalogs.codex_chatgpt)await loadModelCatalog(mode);
 else if(mode==='api'&&!modelCatalogs.api&&state.modelSettings.api.has_key)await loadModelCatalog(mode);
}
async function modelSettingsJob(path,payload){
 if(state.busy)return;
 setBusy(true);$('model-settings-status').textContent='正在处理…';
 if(path==='/api/model-catalog'&&payload.search_mode==='codex_chatgpt')$('codex-catalog-status').textContent='正在读取 Codex 型号目录…';
 try{
  const {job_id}=await api(path,payload);
  let result;
  while(true){const job=await api('/api/job?id='+encodeURIComponent(job_id));$('progress-text').textContent=job.phase;
   if(job.status==='failed')throw new Error(job.error);
   if(job.status==='complete'){result=job.result;break;}
   await new Promise(resolve=>setTimeout(resolve,350));
  }
  if(path==='/api/model-settings'){renderModelSettings(result);await refreshBootstrap();}
  else if(path==='/api/model-catalog'){
   const mode=payload.search_mode==='codex_chatgpt'?'codex_chatgpt':'api';
   const choices=result.choices||result.models.map(id=>({id,name:id}));
   modelCatalogs[mode]={choices,key:apiCatalogKey()};
   modelOptions(mode,$(mode==='api'?'model-id':'codex-model-id').value);
   const message=`读到 ${choices.length} 个型号，请从下拉框选择。${result.message||'列表由当前服务商返回，具体任务是否支持需测试连接。'}`;
   $('model-settings-status').textContent=message;
   if(mode==='codex_chatgpt')$('codex-catalog-status').textContent=message;
  }else if(path==='/api/mineru-sample'){
   $('model-settings-status').textContent=`${result.message} 共 ${result.pages} 页、${result.questions} 题、${result.images} 张原图。${result.notes.join('；')}`;
  }else $('model-settings-status').textContent=result.message;
  return result;
 }catch(error){
  $('model-settings-status').textContent=error.message;notice(error.message,true);
  if(path==='/api/model-catalog'&&payload.search_mode==='codex_chatgpt')$('codex-catalog-status').textContent=error.message+' 已有选择保留，可点击刷新重试。';
 }
 finally{setBusy(false);}
}
$('question-model-mode').onchange=async()=>{
 modelFieldVisibility();const mode=$('question-model-mode').value;
 if(mode==='codex_chatgpt'&&!modelCatalogs.codex_chatgpt)await loadModelCatalog(mode);
 else if(mode==='api'&&!modelCatalogs.api&&state.modelSettings?.api.has_key&&$('model-provider').value===state.modelSettings.api.provider&&$('model-base-url').value===state.modelSettings.api.base_url)await loadModelCatalog(mode);
};
$('model-provider').onchange=()=>{
 $('model-base-url').value=modelTemplates[$('model-provider').value]||'';
 $('model-manual-id').value='';
 modelCatalogs.api=null;modelOptions('api');$('model-api-key').value='';$('model-images').checked=false;
 $('model-key-state').textContent='已切换服务商，请填写对应的 Key，再读取型号。';
};
$('model-base-url').onchange=()=>{modelCatalogs.api=null;modelOptions('api');};
$('save-model-settings').onclick=()=>modelSettingsJob('/api/model-settings',modelSettingsPayload());
$('test-model').onclick=()=>modelSettingsJob('/api/model-test',modelSettingsPayload());
$('load-models').onclick=()=>loadModelCatalog('api');
$('load-codex-models').onclick=()=>loadModelCatalog('codex_chatgpt');
$('mineru-sample').onclick=()=>modelSettingsJob('/api/mineru-sample',{source_id:Number($('mineru-sample-source').value)});
renderQuestionModelBadge();
