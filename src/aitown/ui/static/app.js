const $=id=>document.getElementById(id);
const state={bootstrap:null,session:null,history:null,busy:false,panel:false};
const skillLabels={calculator:'计算器',event_parser:'事件拆分',memory_query:'记忆查询',dialogue_summary:'对话整理',memory_correction:'事务纠正'};
const skillDescriptions={
 calculator:'计算数字四则运算，也可接续前一步的数值结果。',
 event_parser:'按行或分号拆分输入的事项。',
 memory_query:'在当前资料范围查找已整理的记忆。',
 dialogue_summary:'将来源对话整理为要点或时间线，保留来源与原有语义。',
 memory_correction:'按明确的事实编号与新值纠正本人事实。'
};
const feedbackLabels={like_me:'像我',unlike_me:'不像我',fact_wrong:'事实不对',fact_correction:'已纠正事实'};
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function notice(message,error=false){$('notice').textContent=message||'';$('notice').className='notice'+(error?' error':'');$('notice').hidden=!message;}
async function api(path,payload){const options=payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};const response=await fetch(path,options);const body=await response.json();if(!response.ok)throw new Error(body.error||'本地服务暂时不可用');return body;}
function date(value){return value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'时间未记录';}
function setBusy(value){state.busy=value;$('send').disabled=value;$('new-session').disabled=value;$('mode').disabled=value;$('refresh').disabled=value;$('import').disabled=value;$('progress').hidden=!value;document.querySelectorAll('.skill-row input,.delete,.model-form button,.model-form input,.model-form select,.course-form button,.course-form input,.course-form select,.supplement-form button,.supplement-form input,.supplement-form textarea,.question-scope-button,.course-mention-option').forEach(node=>node.disabled=value);if(!value&&$('mineru-sample'))$('mineru-sample').disabled=!$('mineru-sample-source').options.length;}
function detail(title){const node=el('details',undefined,'detail');node.append(el('summary',title));const body=el('div',undefined,'detail-body');node.append(body);return [node,body];}
function sourceDetails(reply){
 const [node,body]=detail(`事实来源 · ${reply.sources.length}`);
 node.open=new URLSearchParams(location.search).get('view')==='sources';
 for(const source of reply.sources){
  const card=el('div',undefined,'source-card');
  card.append(el('div',source.question_id!=null?'题目来源':source.subject==='self'?'本人资料':source.subject?.startsWith('other:')?'他人资料':'知识文档'));
  const meta=el('div',undefined,'source-meta');meta.append(el('span',source.ref));
  if(source.recorded_at||source.timestamp)meta.append(el('span','记录于 '+date(source.recorded_at||source.timestamp)));
  if(source.occurred_at)meta.append(el('span','发生于 '+date(source.occurred_at)));
  card.append(meta,el('p',source.text));
  if(source.question_id!=null&&source.external_url){const link=el('a','打开来源文件');link.href=source.external_url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
  for(const evidence of source.evidence||[]){const quote=el('div',undefined,'source-evidence');quote.append(el('small',`${evidence.source_kind==='user_correction'?'用户纠正':evidence.source_kind?'用户自述':'清洗来源'} · ${date(evidence.timestamp)} · ${evidence.speaker_id||'本人'}`),el('span',evidence.content));card.append(quote);}
  body.append(card);
 }
 return node;
}
function formForFeedback(turn,kind){const box=el('div',undefined,'feedback-form');box.append(el('h4',kind==='fact_wrong'?'记录事实问题或具体纠正':`告诉我哪里${feedbackLabels[kind]}`));const comment=el('textarea');comment.rows=2;comment.placeholder='可选：填写具体批注';comment.setAttribute('aria-label','反馈批注');box.append(comment);const controls=el('div',undefined,'row');const close=el('button','取消','quiet');close.onclick=()=>box.remove();const save=el('button','保存反馈','primary');save.onclick=async()=>{try{await api('/api/feedback',{session:state.session,turn_id:turn.id,kind,comment:comment.value});box.remove();notice('反馈已保存。');await refreshHistory();}catch(error){notice(error.message,true);}};controls.append(close,save);box.append(controls);
 if(kind==='fact_wrong'){const [details,body]=detail('填写具体纠正');const select=el('select');select.setAttribute('aria-label','要纠正的事实');const value=el('textarea');value.rows=2;value.placeholder='输入正确的新值，不填写的内容不会保存';value.setAttribute('aria-label','事实新值');const commit=el('button','保存事实纠正','primary wide');commit.onclick=()=>runJob('/api/correct',{session:state.session,turn_id:turn.id,fact_id:Number(select.value),value:value.value});body.append(select,value,commit,el('p','只纠正所选事实。当前资料范围外的记录不可修改。','muted'));box.append(details);api('/api/maintenance?session='+encodeURIComponent(state.session)).then(data=>{if(!data.facts.length){select.append(el('option','当前范围没有可纠正的本人事实'));commit.disabled=true;}for(const fact of data.facts){const option=el('option',`${fact.attribute}：${fact.value}`);option.value=fact.id;select.append(option);}}).catch(error=>notice(error.message,true));}
 return box;}
function renderTurns(){const container=$('turns');container.replaceChildren();$('welcome').hidden=!!state.history?.turns.length;for(const turn of state.history?.turns||[]){const section=el('article',undefined,'turn');section.append(el('div',turn.question,'user-message'));const label=el('div',undefined,'assistant-label');label.append(el('span','A','avatar'),el('span',state.bootstrap.name));section.append(label,el('div',turn.answer,'answer'));const reply=turn.reply;if(reply){section.append(el('p',reply.source_changed?'来源已失效 · 请重新提问':reply.output_kind==='generated'?'真实模型回答':reply.output_kind==='offline'?'离线结果 · 原文摘录与工具执行':'程序结果 · 本轮未形成模型回答','reply-note'));if(reply.sources.length)section.append(sourceDetails(reply));if(reply.profile.length||reply.style.length){const [details,body]=detail('表达参考 · 与事实依据分开');for(const profile of reply.profile)body.append(el('p',profile.text));for(const style of reply.style)body.append(el('p',style,'muted'));section.append(details);}if(reply.skills.length){const [details,body]=detail(`技能结果 · ${reply.skills.length}`);for(const skill of reply.skills){body.append(el('p',`${skillLabels[skill.name]||skill.name}：${skill.ok?'执行成功':'执行失败'}`));const pre=el('pre',skill.ok?JSON.stringify(skill.data,null,2):skill.error);pre.className='debug';body.append(pre);}section.append(details);}if(reply.warnings?.length)section.append(el('p',reply.warnings.join('；'),'warning'));const [debug,body]=detail('运行详情');debug.classList.add('debug');body.append(el('pre',JSON.stringify({终止原因:reply.stop_reason,...reply.debug},null,2)));section.append(debug);}
 const tools=el('div',undefined,'reply-tools');for(const kind of ['like_me','unlike_me','fact_wrong']){const button=el('button',feedbackLabels[kind]);button.onclick=()=>{section.querySelector('.feedback-form')?.remove();section.append(formForFeedback(turn,kind));};tools.append(button);}section.append(tools);if(turn.feedback.length)section.append(el('div',turn.feedback.map(feedback=>feedbackLabels[feedback.kind]||'已保存反馈').join(' · '),'saved-feedback'));container.append(section);}if(state.history?.pending){container.append(el('div',state.history.pending_question,'user-message'));const recover=el('button','继续未完成的对话','primary');recover.onclick=()=>runJob('/api/resume',{session:state.session});container.append(recover);}}
function renderBootstrap(){const data=state.bootstrap;$('data-badge').textContent=data.data_label;$('welcome-note').textContent=data.data==='synthetic'?'这里的活动与表达样例均为合成素材。':data.data==='wechat'?'当前使用已复核微信资料的独立副本，回答可展开清洗来源。':'当前使用显式选择的本地资料范围。';$('suggestions').hidden=data.data!=='synthetic';$('mode-badge').textContent=data.mode==='live'?'真实模型 · 本机':'离线演示';$('mode-badge').classList.toggle('live',data.mode==='live');$('mode').value=data.mode;$('mode').querySelector('[value=live]').disabled=!data.live_available;$('model-note').textContent=data.mode==='live'?`当前模型：${data.model}。输入、会话和选中的依据交给本机模型。`:'离线展示资料摘录和工具结果，不生成角色回答。';$('composer-note').textContent=data.mode==='live'?'事实请核对来源。Enter 发送 · Shift + Enter 换行':'离线演示展示原文与工具结果，不是模型生成回答。Enter 发送 · Shift + Enter 换行';$('boundary').textContent=data.data==='synthetic'?'当前使用独立合成库；不读取微信原始导出、媒体或私有映射。':data.data==='wechat'?'当前使用已复核微信资料的独立副本；会话与修改仅写入副本，不提供原微信、媒体或映射文件链接。':'当前使用启动时指定的应用库；来源只展示已复核的清洗正文或显式用户输入，不提供原始文件链接。';const selected=$('scope').value||data.default_scope;$('scope').replaceChildren();const blank=el('option','仅知识资料，不加载个人范围');blank.value='';$('scope').append(blank);for(const scope of data.scopes){const option=el('option',scope==='synthetic'?'合成资料':scope);option.value=scope;$('scope').append(option);}$('scope').value=selected;$('session-count').textContent=data.sessions.length;renderSessions();}
function renderSessions(){const nav=$('sessions');nav.replaceChildren();for(const session of state.bootstrap.sessions){const button=el('button',session.title,session.id===state.session?'active':'');button.title=session.title;button.onclick=()=>chooseSession(session.id);nav.append(button);}}
async function chooseSession(id){state.session=id;localStorage.setItem('anotherme-session-'+state.bootstrap.data,id);const url=new URL(location.href);url.searchParams.set('session',id);history.replaceState(null,'',url);renderSessions();await refreshHistory();if(state.panel)await refreshMaintenance();}
async function refreshHistory(){const id=state.session;if(!id)return;try{const history=await api('/api/history?session='+encodeURIComponent(id));if(state.session!==id)return;state.history=history;$('session-title').textContent=history.session.title;$('current-scope').textContent=history.session.scope==='synthetic'?'资料范围：合成资料':history.session.scope?`资料范围：${history.session.scope}`:'仅查询知识资料';if(typeof renderQuestionScope==='function')renderQuestionScope();renderTurns();$('conversation').scrollTop=$('conversation').scrollHeight;}catch(error){notice(error.message,true);}}
async function refreshBootstrap(){state.bootstrap=await api('/api/bootstrap');renderBootstrap();if(typeof renderQuestionModelBadge==='function')renderQuestionModelBadge();if(typeof renderCourseCatalog==='function')renderCourseCatalog();}
async function runJob(path,payload){if(state.busy)return;notice('');setBusy(true);try{const {job_id}=await api(path,payload);while(true){const job=await api('/api/job?id='+encodeURIComponent(job_id));$('progress-text').textContent=job.phase;if(job.status==='complete'){if(job.result.warning)notice(job.result.warning);else if(path!=='/api/chat'&&path!=='/api/resume')notice('已完成。');break;}if(job.status==='failed')throw new Error(job.error);await new Promise(resolve=>setTimeout(resolve,350));}setBusy(false);await refreshBootstrap();await refreshHistory();if(state.panel)await refreshMaintenance();return true;}catch(error){setBusy(false);notice(error.message,true);await refreshHistory();return false;}}
async function send(){const question=$('message').value.trim();if(!question||state.busy)return;const session=state.session;const scene=$('scene').value||null;$('message').value='';const success=await runJob('/api/chat',{question,session,scene});if(!success&&!state.history?.pending)$('message').value=question;}
async function refreshMaintenance(){if(!state.session)return;try{const data=await api('/api/maintenance?session='+encodeURIComponent(state.session));$('documents').replaceChildren();for(const document of data.documents){const row=el('div',undefined,'document-row');const info=el('div',document.name);info.append(el('small',`${document.chars} 字`));const remove=el('button','删除','delete');remove.onclick=()=>{if(window.confirm(`删除“${document.name}”？重新导入原文件可恢复资料；现有引用将失效。`))runJob('/api/delete-document',{id:document.id});};row.append(info,remove);$('documents').append(row);}if(!data.documents.length)$('documents').append(el('p','还没有独立导入的 TXT / MD。','empty'));$('skills').replaceChildren();for(const skill of data.skills){const row=el('div',undefined,'skill-row');const label=el('label');const toggle=el('input');toggle.type='checkbox';toggle.checked=skill.enabled;toggle.setAttribute('aria-label',skillLabels[skill.name]||skill.name);toggle.onchange=async()=>{try{await api('/api/skill',{name:skill.name,enabled:toggle.checked});notice('技能设置已保存。');}catch(error){toggle.checked=!toggle.checked;notice(error.message,true);}};const text=el('div',skillLabels[skill.name]||skill.name);text.append(el('small',skillDescriptions[skill.name]||skill.description));label.append(toggle,text);row.append(label);$('skills').append(row);}$('memories').replaceChildren();for(const memory of data.memories||[]){const card=el('div',memory.summary,'fact-card');const category=({plan:'历史计划',preference:'历史偏好',event:'事件',long_term:'长期信息'})[memory.category]||memory.category;card.append(el('small',`${category} · ${date(memory.recorded_at)} · ${memory.source_count} 条清洗来源`));$('memories').append(card);}if(!data.memories?.length)$('memories').append(el('p','当前范围没有可展示的本人训练记忆。','empty'));$('facts').replaceChildren();for(const fact of data.facts){const card=el('div',fact.value,'fact-card');card.append(el('small',`事实 ${fact.id} · ${fact.attribute}`));$('facts').append(card);}if(!data.facts.length)$('facts').append(el('p','当前范围没有本人事实。','empty'));$('profile').replaceChildren();for(const claim of data.profile){const card=el('div',claim.text,'profile-card');card.append(el('small',claim.basis==='observed'?'训练表达统计':'有来源自述'));$('profile').append(card);}if(!data.profile.length)$('profile').append(el('p','当前范围没有启用的表达画像。','empty'));setBusy(state.busy);}catch(error){notice(error.message,true);}}
async function toggleSettings(open){state.panel=open?'settings':false;$('settings').hidden=!open;$('materials').hidden=true;if(open&&!state.busy){try{await refreshMaintenance();if(typeof refreshModelSettings==='function'&&!state.busy)await refreshModelSettings();}catch(error){notice(error.message,true);}}}
async function openMaterials(view='course'){
 state.panel='materials';$('settings').hidden=true;$('materials').hidden=false;
 const course=view==='course';$('course-library-view').hidden=!course;$('personal-library-view').hidden=course;
 for(const [id,active] of [['course-library-tab',course],['personal-library-tab',!course]]){$(id).classList.toggle('active',active);$(id).setAttribute('aria-pressed',String(active));}
 if(course&&typeof showCourseStatus==='function')await showCourseStatus();else await refreshMaintenance();
}
$('new-session').onclick=async()=>{try{const session=await api('/api/session',{scope:$('scope').value});await refreshBootstrap();await chooseSession(session.id);notice('');}catch(error){notice(error.message,true);}};
$('send').onclick=send;$('message').onkeydown=event=>{if(typeof handleQuestionMentionKey==='function'&&handleQuestionMentionKey(event))return;if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send();}};
document.querySelectorAll('[data-question]').forEach(button=>button.onclick=()=>{$('message').value=button.dataset.question;$('message').focus();});
$('open-settings').onclick=()=>toggleSettings(true);$('settings-button').onclick=()=>toggleSettings(true);$('close-settings').onclick=()=>toggleSettings(false);
$('open-materials').onclick=()=>openMaterials('personal');$('close-materials').onclick=()=>toggleSettings(false);
$('course-library-tab').onclick=()=>openMaterials('course');$('personal-library-tab').onclick=()=>openMaterials('personal');
$('mode').onchange=()=>runJob('/api/mode',{mode:$('mode').value});$('refresh').onclick=()=>runJob('/api/refresh',{});
$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;if(file.size>2000000){notice('文件超过 2 MB，请精简后导入。',true);return;}$('document-name').value=file.name;$('document-content').value=await file.text();};
$('import').onclick=()=>runJob('/api/import',{name:$('document-name').value,content:$('document-content').value});
async function boot(){try{await refreshBootstrap();const cached=new URLSearchParams(location.search).get('session')||localStorage.getItem('anotherme-session-'+state.bootstrap.data);let session=state.bootstrap.sessions.find(session=>session.id===cached)||state.bootstrap.sessions[0];if(!session){session=await api('/api/session',{scope:state.bootstrap.default_scope});await refreshBootstrap();}await chooseSession(session.id);if(new URLSearchParams(location.search).get('settings')==='1')await toggleSettings(true);else if(typeof maybeShowSetup==='function')await maybeShowSetup();if(state.bootstrap.retrieval_error)notice('检索模型初始化失败，个人事实查询将停止生成。请检查本地模型路径。',true);}catch(error){notice(error.message,true);}}
boot();

function openQuestionImage(url,label){
 const dialog=el('dialog',undefined,'image-viewer');
 dialog.setAttribute('aria-label',label);
 const toolbar=el('div',undefined,'image-toolbar');
 const title=el('strong',label);const size=el('button','原始尺寸');const close=el('button','关闭');
 const original=el('a','新窗口打开');original.href=url;original.target='_blank';original.rel='noopener noreferrer';
 const scroll=el('div',undefined,'image-scroll');const image=el('img');image.src=url;image.alt=label;scroll.append(image);
 size.onclick=()=>{const actual=scroll.classList.toggle('actual-size');size.textContent=actual?'适应窗口':'原始尺寸';};
 close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());
 toolbar.append(title,size,original,close);dialog.append(toolbar,scroll);document.body.append(dialog);dialog.showModal();
}
function questionPageLabel(page){
 const kind=({paragraph:'段落',line:'行',page:'文件第'})[page.location_kind]||'位置';
 return `${kind} ${page.ordinal}${page.location_kind==='page'?' 页':''}${page.printed_label?`（卷面页码 ${page.printed_label}）`:''}`;
}
function questionCards(questions){
 const box=el('div',undefined,'question-cards');
 for(const q of questions){
  const card=el('section',undefined,'question-card');
  const exam=({final:'期末',midterm:'期中'})[q.exam]||q.exam||'考试类别未确认';
  const category=({exam:'试卷',recollection:'回忆题',practice:'练习',unknown:'类别未确认'})[q.category]||q.category;
  card.append(el('h3',`${q.year||'年份未确认'} · 原题 ${q.number}`),el('small',[exam,category,q.question_type?q.question_type+'题':'题型未确认',q.topics.join(' / ')].filter(Boolean).join(' · ')));
  const textQuestion=q.content_format==='markdown'||q.content_format==='plain';
  if(textQuestion)card.append(renderQuestionText(q.text||'',q.content_format));
  const sourceLines=(q.source_pages||[]).filter(page=>page.location_kind==='line').map(page=>page.ordinal);
  if(textQuestion&&sourceLines.length)card.append(el('p',`来源位置：原文件第 ${Math.min(...sourceLines)}–${Math.max(...sourceLines)} 行`,'muted'));
  for(let i=0;i<q.images.length;i++){
   const preview=q.image_previews?.find(p=>p.index===i);const kind=({crop:'原题裁剪图',page:'原卷页面',embedded:'原件内嵌图片',image:'题目附图'})[preview?.kind]||'题目图片';
   const location=preview?.page?questionPageLabel({ordinal:preview.page,location_kind:preview.location_kind,printed_label:preview.printed_label}):'位置待核对';
   const label=`${kind} ${i+1}/${q.images.length} · 原题 ${q.preview_number||q.number} · ${q.converted_from_word?'Word 转 PDF · ':''}${location}`;
   const figure=el('figure',undefined,'question-figure');const button=el('button',undefined,'question-image-button');
   const url=`/api/question-image?id=${q.id}&index=${i}`;const img=el('img');img.src=url;img.alt=label;img.loading='lazy';button.setAttribute('aria-label',`放大查看 ${label}`);button.onclick=()=>openQuestionImage(url,label);button.append(img);
   img.onerror=()=>figure.replaceChildren(el('p','原题截图加载失败，请打开原文件查看。','muted'));
   figure.append(button,el('figcaption',label+' · 点击放大'));card.append(figure);
  }
  if(!textQuestion&&!q.images.length)card.append(el('p','当前没有可展示的原题截图，可打开原文件核对。','muted'));
  else if(q.image_previews?.length&&q.image_previews.every(p=>p.kind==='embedded'))card.append(el('p','此处为原文件中的附图，完整题目请打开原文件查看。','muted'));
  if(q.missing.length)card.append(el('p',q.missing.join('；'),'warning'));
  if(q.match_reason)card.append(el('p',q.match_reason));
  card.append(el('small',q.answer_available?'有来源答案':'未发现可确认配对的来源答案'));
  const pages=q.source_pages||[];
  const pageLinks=el('div',undefined,'question-page-links');
  for(const page of pages.filter(p=>p.image&&p.location_kind==='page')){const button=el('button',`查看原页 · ${questionPageLabel(page)}`);button.onclick=()=>openQuestionImage(`/api/question-page?id=${q.id}&page=${page.ordinal}`,`原题 ${q.number} · ${questionPageLabel(page)}`);pageLinks.append(button);}
  if(pageLinks.childElementCount)card.append(pageLinks);
  const origins=q.origins||[{source_id:q.source_id,path:q.path,member:q.member,url:q.url,number:q.number,pages:q.pages,year:q.year}];
  if(origins.length>1)card.append(el('p',`同题已合并 · ${origins.length} 个出处`,'muted'));
  for(const origin of origins){
   const kind=origin.file_kind||'raw';
   if(kind==='remote'&&!/^https?:\/\//i.test(origin.url||'')){card.append(el('p','本地原件已丢失，请重新上传同名文件并选择替换。','muted'));continue;}
   const action=kind==='converted_pdf'?'查看 Word 转换 PDF':kind==='remote'?(origin.member?'下载来源压缩包':'获取来源文件'):`打开${origin.member?'解压后原文件':'原文件'}`;
   const link=el('a',`${action} · ${origin.member||origin.path.split('/').pop()} · ${origin.year||'年份未确认'} · 原题 ${origin.number}`);
   link.href=kind==='remote'?origin.url:`/api/question-source?id=${q.id}&source=${origin.source_id}`;link.target='_blank';link.rel='noopener noreferrer';card.append(link);
  }
  if(/^https?:\/\//i.test(q.url||'')){const remote=el('a','查看网页来源');remote.href=q.url;remote.target='_blank';remote.rel='noopener noreferrer';card.append(remote);}
  if(q.answers){const [node,body]=detail(q.answers.length?'已有来源答案':'未发现可确认配对的答案');for(const answer of q.answers){body.append(el('p',answer.text));if(answer.missing?.length)body.append(el('p',answer.missing.join('；'),'warning'));for(const origin of answer.origins||[answer]){const kind=({paragraph:'段落',line:'行',page:'页'})[origin.location_kind]||'位置';const action=origin.file_kind==='converted_pdf'?'查看答案转换 PDF':origin.file_kind==='remote'?(origin.member?'下载答案来源压缩包':'获取答案来源文件'):'打开答案原件';const source=el('a',`${action} · ${origin.member||origin.path.split('/').pop()} · ${kind} ${origin.locations.join(', ')}`);source.href=origin.file_kind==='remote'?origin.url:`/api/question-source?id=${q.id}&source=${origin.source_id}`;source.target='_blank';source.rel='noopener noreferrer';body.append(source);}}card.append(node);}
  box.append(card);
 }
 return box;
}
const renderTurnsWithEvidence=renderTurns;
renderTurns=function(){
 renderTurnsWithEvidence();
 document.querySelectorAll('#turns .turn').forEach((section,i)=>{
  const reply=state.history.turns[i].reply;
  if(reply?.questions?.length)section.insertBefore(questionCards(reply.questions),section.querySelector('.reply-tools'));
  if(reply?.output_kind==='question_selected'&&!reply.source_changed)section.querySelector('.reply-note').textContent='模型已筛选本地题库候选 · 请核对题目与来源';
 });
};
