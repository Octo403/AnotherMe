let wechatPreview=null;
function wechatSpeaker(speaker){return speaker.startsWith('contact:')?'联系人 '+speaker.slice(8):speaker;}
async function wechatAction(path,payload){
 try{const result=await api(path,payload);await refreshWechat();return result;}
 catch(error){notice(error.message,true);return null;}
}
async function refreshWechat(){
 try{
  const data=await api('/api/wechat-sync');
  $('wechat-storage').textContent='清洗资料与同步配置保存在：'+data.database;
  const box=$('wechat-sources');box.replaceChildren();
  for(const source of data.sources){
   const card=el('div',undefined,'wechat-source');
   card.append(el('strong',source.label),el('p',source.phase||'尚未同步','muted'));
   const counts=source.counts;
   card.append(el('small',`待复核 ${counts.pending||0} · 已入库 ${counts.approved||0} · 已忽略 ${counts.rejected||0}`));
   if(source.last_sync)card.append(el('small',`最近同步 ${date(source.last_sync)}：新增 ${source.added}，变化 ${source.changed}，跳过 ${source.skipped}`));
   if(source.error)card.append(el('p',source.error,'error'));
   const actions=el('div',undefined,'row wechat-actions');
   const running=['running','waiting','stopping'].includes(source.state);
   const once=el('button','同步一次','quiet');once.disabled=running;
   once.onclick=()=>wechatAction('/api/wechat-start',{id:source.id});
   const repeat=el('button',source.mode==='tool'?'定时采集':'定时读取文件','quiet');repeat.disabled=running;
   repeat.onclick=()=>wechatAction('/api/wechat-start',{id:source.id,repeat:true});
   const stop=el('button','停止','quiet');stop.disabled=!running;
   stop.onclick=()=>wechatAction('/api/wechat-stop',{id:source.id});
   const preview=el('button','复核新增资料','quiet');preview.disabled=!(counts.pending>0);
   preview.onclick=()=>loadWechatPreview(source.id);
   const library=el('button','查看已收录','quiet');library.disabled=!(counts.approved>0);
   library.onclick=()=>loadWechatLibrary(source.id,source.label);
   const use=el('button','使用此资料范围','quiet');
   use.onclick=async()=>{if(state.busy)return;try{const session=await api('/api/session',{scope:source.scope});await refreshBootstrap();await chooseSession(session.id);notice('已切换到 '+source.label+'；仅已复核资料可用于回答。');}catch(error){notice(error.message,true);}};
   const remove=el('button','删除来源与资料','quiet');remove.disabled=running;
   remove.onclick=async()=>{if(!window.confirm('删除此来源及其清洗资料、关联记忆与事实？你选择的原始导出文件会保留。'))return;const result=await wechatAction('/api/wechat-delete',{id:source.id});if(result){if(wechatPreview?.source.id===source.id){wechatPreview=null;$('wechat-preview').hidden=true;}await refreshBootstrap();await refreshMaintenance();}};
   actions.append(once,repeat,stop,preview,library,use,remove);card.append(actions);box.append(card);
  }
  if(!data.sources.length)box.append(el('p','还没有微信同步来源。先选择一个会话试用。','empty'));
 }catch(error){notice(error.message,true);}
}
async function loadWechatPreview(id){
 try{
  wechatPreview=await api('/api/wechat-preview?id='+encodeURIComponent(id));
  const box=$('wechat-preview');box.replaceChildren();box.hidden=false;
  box.append(el('h4',wechatPreview.source.label+' · 待复核资料'),el('p','每页最多 50 条。勾选要处理的正文；拟确认的本人记忆会单独展示。','muted'));
  const selectAll=el('label');const allCheck=el('input');allCheck.type='checkbox';
  allCheck.onchange=()=>box.querySelectorAll('.wechat-message-check').forEach(node=>node.checked=allCheck.checked);
  selectAll.append(allCheck,el('span','勾选本页全部正文（记忆仍需逐项确认）'));box.append(selectAll);
  for(const item of wechatPreview.items){
   const card=el('div',undefined,'wechat-review-card');
   const select=el('input');select.type='checkbox';select.className='wechat-message-check';select.dataset.id=String(item.id);
   const label=el('label');label.append(select,el('span',`${date(item.expected.timestamp)} · ${item.expected.direction==='outgoing'?'本人':wechatSpeaker(item.expected.speaker_id)}`));
   card.append(label,el('p',item.expected.content));
   if(item.memory){
    const memoryLabel=el('label');const memoryCheck=el('input');memoryCheck.type='checkbox';memoryCheck.className='wechat-memory-check';memoryCheck.dataset.id=String(item.id);
    memoryLabel.append(memoryCheck,el('span','同时确认本人记忆：'+item.memory.summary));card.append(memoryLabel);
    const category=({preference:'历史偏好',long_term:'长期信息',event:'事件',plan:'计划（不代表已完成）'})[item.memory.category]||item.memory.category;
    card.append(el('small',`${category} · ${item.memory.value||item.memory.summary}${item.memory.category==='plan'?' · '+(item.memory.occurred_at?'计划时间 '+date(item.memory.occurred_at):'计划时间未确定'):''}`));
   }else card.append(el('small','仅收录正文；没有可自动确认的本人记忆。'));
   box.append(card);
  }
  if(!wechatPreview.items.length)box.append(el('p','没有待复核内容。','empty'));
  const actions=el('div',undefined,'row wechat-actions');
  for(const [action,text] of [['approve','确认所选'],['reject','忽略所选']]){
   const button=el('button',text,action==='approve'?'primary':'quiet');button.onclick=()=>reviewWechat(action);actions.append(button);
  }
  const refresh=el('button','刷新 / 下一页','quiet');refresh.onclick=()=>loadWechatPreview(id);actions.append(refresh);
  box.append(actions);box.scrollIntoView({block:'nearest'});
 }catch(error){notice(error.message,true);}
}
async function loadWechatLibrary(id,label,offset=0){
 try{
  const data=await api('/api/wechat-library?id='+encodeURIComponent(id)+'&offset='+offset);
  wechatPreview=null;const box=$('wechat-preview');box.replaceChildren();box.hidden=false;
  box.append(el('h4',label+' · 已收录正文'),el('p','已收录正文保留原话。只有单独确认的本人记忆用于个人事实查找；未解释的句子不会自动变成事实。','muted'));
  for(const item of data.items){const card=el('div',undefined,'wechat-review-card');card.append(el('small',`${date(item.timestamp)} · ${item.direction==='outgoing'?'本人':wechatSpeaker(item.speaker_id)} · 来源 ${item.id} · 事件 ${item.episode_id}`),el('p',item.content));box.append(card);}
  const actions=el('div',undefined,'row wechat-actions');
  if(offset){const prev=el('button','上一页','quiet');prev.onclick=()=>loadWechatLibrary(id,label,Math.max(0,offset-50));actions.append(prev);}
  if(data.items.length===50){const next=el('button','下一页','quiet');next.onclick=()=>loadWechatLibrary(id,label,offset+50);actions.append(next);}
  box.append(actions);box.scrollIntoView({block:'nearest'});
 }catch(error){notice(error.message,true);}
}
async function reviewWechat(action){
 if(state.busy||!wechatPreview)return;
 const selected=new Set([...document.querySelectorAll('.wechat-message-check:checked')].map(node=>Number(node.dataset.id)));
 const memories=new Set([...document.querySelectorAll('.wechat-memory-check:checked')].map(node=>Number(node.dataset.id)));
 const items=wechatPreview.items.filter(item=>selected.has(item.id)).map(item=>({...item,confirm_memory:action==='approve'&&memories.has(item.id)}));
 if(!items.length){notice('请先勾选本页要处理的资料。');return;}
 const id=wechatPreview.source.id;
 if(await runJob('/api/wechat-review',{id,action,items})){await loadWechatPreview(id);await refreshWechat();notice(action==='approve'?'所选资料已保存；点击“使用此资料范围”开始对话。':'所选资料已忽略，重复同步不会再次弹出。');}
}
$('wechat-mode').onchange=()=>{
 const tool=$('wechat-mode').value==='tool';$('wechat-file-fields').hidden=tool;$('wechat-tool-fields').hidden=!tool;
};
$('wechat-save').onclick=async()=>{
 const result=await wechatAction('/api/wechat-source',{
  label:$('wechat-label').value,chat_name:$('wechat-chat').value,mode:$('wechat-mode').value,
  json_path:$('wechat-json').value,tool_directory:$('wechat-tool').value,python_path:$('wechat-python').value,
  db_directory:$('wechat-db').value,interval_seconds:Number($('wechat-interval').value)
 });
 if(result){await refreshBootstrap();notice('会话来源已保存。点击“同步一次”检查导出内容。');}
};
setInterval(()=>{if(state.panel==='materials'&&!$('personal-library-view').hidden)refreshWechat();},4000);
