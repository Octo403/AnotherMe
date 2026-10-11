// The setup guide uses the same settings API and secret handling as Settings.
function renderSetupDetails(data){
 const documents=data.documents;
 if(!documents)return;
 const text=documents.libreoffice?'本地转换器已就绪：PDF、DOC、DOCX、MD、TXT 可直接导入。Word 转换后的字体与分页请核对原件。':'PDF、MD、TXT 可直接处理；DOCX 可提取文字与附图。DOC 原排版转换需要完整版体验包或本机 Word / LibreOffice。';
 $('setup-document-status').textContent=text;
 $('document-compatibility').textContent=text;
 $('setup-storage').textContent='课程资料保存到：'+documents.directory+'。退出后仍保留；备份请复制整个 user-data 文件夹。';
}
async function showSetup(){
 $('first-run-setup').hidden=false;
 await toggleSettings(true);
 if(state.modelSettings?.search_mode==='none'){
  $('question-model-mode').value='api';
  modelFieldVisibility();
 }
 $('setup-status').textContent='可先配置模型，也可以先导入资料、使用本地筛选。';
}
let setupShown=false;
async function maybeShowSetup(){if(state.bootstrap?.setup_required&&!setupShown){setupShown=true;await showSetup();}}
document.addEventListener('DOMContentLoaded',()=>{if(state.history)maybeShowSetup();});
$('reopen-setup').onclick=showSetup;
$('finish-setup').onclick=async()=>{
 if(state.busy)return;
 const payload=modelSettingsPayload();
 $('setup-status').textContent='正在测试配置…';
 if(payload.search_mode==='api'){
  const tested=await modelSettingsJob('/api/model-test',payload);
  if(!tested){$('setup-status').textContent='连接测试失败，检查下方提示后重试。';return;}
 }
 const saved=await modelSettingsJob('/api/model-settings',payload);
 if(!saved){$('setup-status').textContent='配置未保存，检查下方提示后重试。';return;}
 try{
  await api('/api/setup-complete',{});await refreshBootstrap();
  $('setup-status').textContent='配置已完成，可以打开课程资料并开始收集。';
  $('first-run-setup').hidden=true;
  await openMaterials('course');
 }catch(error){$('setup-status').textContent=error.message;}
};
$('skip-setup').onclick=async()=>{
 if(state.busy)return;
 try{
  const payload=modelSettingsPayload();payload.search_mode='none';
  if(!await modelSettingsJob('/api/model-settings',payload))return;
  await api('/api/setup-complete',{});await refreshBootstrap();
  $('first-run-setup').hidden=true;await openMaterials('course');
  notice('可先收集和查询本地资料；需要第二轮筛题时，在设置中配置模型。');
 }catch(error){$('setup-status').textContent=error.message;}
};
