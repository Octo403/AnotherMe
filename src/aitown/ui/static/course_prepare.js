let courseStatusTimer=null;
let courseStatusRequest=0;
let courseNameEdited=false;
let courseLaunching=false;
let courseCatalogFilter='';
const courseFileStates={discovered:'待处理',processing:'处理中',downloaded:'待解析',ready:'已解析',partial:'部分解析',unsupported:'暂不支持',needs_review:'待审校',failed:'失败',expanded:'已解压',skipped:'已跳过'};
function selectedPreparationCourse(){
 const value=$('course-name').value.trim();
 return (state.bootstrap?.question_courses||[]).find(course=>course.name===value||course.id===value);
}
function renderCourseCatalog(){
 const courses=state.bootstrap?.question_courses||[];
 if(!$('course-name').value){const current=state.history?.session?.question_course;const course=courses.find(c=>c.id===current)||courses.find(c=>c.name==='集合与图论')||courses[0];if(course)$('course-name').value=course.name;}
 updateCourseHint();
 renderCourseOptions(courseCatalogFilter);
}
function closeCoursePicker(){
 $('course-picker-results').hidden=true;
 $('course-name').setAttribute('aria-expanded','false');$('browse-courses').setAttribute('aria-expanded','false');
}
function renderCourseOptions(query=''){
 courseCatalogFilter=query;
 const courses=state.bootstrap?.question_courses||[],needle=query.trim().toLocaleLowerCase();
 const matches=courses.filter(course=>course.name.toLocaleLowerCase().includes(needle)||course.id.toLocaleLowerCase().includes(needle));
 const options=$('available-course-names');options.replaceChildren();
 for(const course of matches){
  const option=el('button',course.name,'course-catalog-option');option.type='button';option.setAttribute('role','option');option.setAttribute('aria-selected',String(course===selectedPreparationCourse()));option.disabled=state.busy;
  option.append(el('small',`${course.files||0} 份资料 · ${course.questions||0} 道已整理题目`));
  option.onclick=()=>{$('course-name').value=course.name;courseInputChanged();$('course-name').focus();closeCoursePicker();showCourseStatus();};
  option.onkeydown=event=>{
   const index=matches.indexOf(course);
   if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();options.children[(index+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length]?.focus();}
   else if(event.key==='Escape'){event.preventDefault();$('course-name').focus();closeCoursePicker();}
  };
  options.append(option);
 }
 if(!matches.length)options.append(el('p','没有匹配的课程，可在下方补充资料来新建课程。','muted'));
 $('course-catalog-results').textContent=`显示 ${matches.length} / ${courses.length} 门课程${needle?' · 按输入筛选':' · 可滚动浏览'}`;
}
function openCoursePicker(query=''){
 renderCourseOptions(query);$('course-picker-results').hidden=false;
 $('course-name').setAttribute('aria-expanded','true');$('browse-courses').setAttribute('aria-expanded','true');
}
function courseInputChanged(){
 courseNameEdited=true;++courseStatusRequest;clearTimeout(courseStatusTimer);updateCourseHint();
 $('course-progress').textContent='选择课程后读取收集状态';$('course-progress-bar').hidden=true;$('course-files').replaceChildren();
 $('course-supplement-status').textContent='';
}
function syncPreparationCourse(course){
 if(course&&!courseNameEdited&&$('course-name').value!==course.name){$('course-name').value=course.name;updateCourseHint();showCourseStatus();}
}
function updateCourseHint(){
 const course=selectedPreparationCourse();
 const name=$('course-name').value.trim();
 $('course-supplement-target').textContent=name?`补充到：${course?.name||name}${course?'':'（将新建本地课程）'}`:'请先填写上方课程名称，再添加资料。';
 $('course-storage-location').textContent='本地资料位置：'+(state.bootstrap?.question_bank_directory||'data/question_bank/');
 $('course-catalog-note').textContent=course?`${course.name}：${course.files||0} 份资料，已整理 ${course.questions||0} 道题目。`:'未收录该课程；可在下方添加网址或上传文件，自动新建本地课程。';
}
function renderCourseFiles(course,files){
 const list=$('course-files');list.replaceChildren();
 for(const file of files){
  const row=el('div',file.name,'course-file');
  row.append(el('small',`${courseFileStates[file.status]||file.status} · ${file.questions||0} 条题目记录 · ${{upload:'本地上传',url:'补充网址'}[file.origin_kind]||'公开目录'}${file.member?' · 压缩包内文件':''}${file.remote_removed?' · 远端已移除':''}`));
  if(file.error)row.append(el('small',file.error));
  for(const note of file.notes||[])row.append(el('small',note));
  if(file.local_available){const link=el('a',file.file_kind==='converted_pdf'?'打开本地转换 PDF':'打开本地原件');link.href='/api/course-source?course='+encodeURIComponent(course.id)+'&source='+file.id;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}
  if(/^https?:\/\//i.test(file.url||'')){const link=el('a','公开来源');link.href=file.url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}
  if(file.status==='failed'){
   const retry=el('button','重试此文件','quiet');retry.type='button';retry.disabled=state.busy;
   retry.onclick=async()=>{retry.disabled=true;try{const job=await api('/api/course-retry',{course:course.id,source:file.id});notice(`重试任务 ${job.job_id} 已提交。`);await showCourseStatus();}catch(error){notice(error.message,true);}finally{retry.disabled=state.busy;}};row.append(retry);
  }
  for(const attachment of file.links||[]){
   const button=el('button','＋ 添加网页资料链接 · '+(attachment.label?.trim()||attachment.url),'quiet course-attachment');button.type='button';button.disabled=state.busy;button.title=attachment.url;
   button.onclick=()=>supplementCourse('urls',[attachment.url],course.name);row.append(button);
  }
  list.append(row);
 }
 if(!files.length)list.append(el('p','尚无资料文件，输入课程名后开始收集。','muted'));
}
async function showCourseStatus(){
 clearTimeout(courseStatusTimer);courseStatusTimer=null;
 if(state.busy){courseStatusTimer=setTimeout(showCourseStatus,2500);return;}
 const course=selectedPreparationCourse();if(!course){updateCourseHint();return;}
 const request=++courseStatusRequest;
 try{
  const result=await api('/api/course-status?course='+encodeURIComponent(course.id));
  if(request!==courseStatusRequest||selectedPreparationCourse()?.id!==course.id)return;
  const c=result.coverage;const job=result.jobs[0];
  const status={ready:'可检索',partial:'部分完成',needs_preparation:'尚未准备',queued:'等待处理',running:'正在处理',complete:'本次处理完成',interrupted:'处理已中断',failed:'处理失败'};
  const stages={queued:'等待处理','queued-update':'更新资料',discover:'发现资料',download:'获取原件',parse:'解析资料',index:'建立检索索引',finished:'结束'};
  const counts=Object.entries(c.states||{}).map(([name,count])=>`${courseFileStates[name]||name} ${count}`).join(' · ');
  const current=(result.files||[]).find(file=>file.id===job?.current_source);
  $('course-progress').textContent=`${course.name} · ${status[result.status]||result.status}\n已发现 ${c.discovered_files||0} 份资料 · 可检索 ${c.questions||0} 题\n${counts}\n本地原件 ${c.raw_retained||0} 份 · 已清理 ${c.raw_deleted||0} 份${job?`\n任务 ${job.id}：${status[job.status]||job.status} · ${stages[job.stage]||job.stage}\n已处理 ${job.done_ids.length}/${job.source_ids.length} 份（包含失败项）${current?'\n当前文件：'+current.name:''}${job.error?'\n'+job.error:''}`:''}${result.status==='partial'&&!c.questions?'\n已有处理结果，尚未识别可检索题目；请核对格式或待审校项。':''}`;
  const bar=$('course-progress-bar');bar.hidden=!job?.source_ids.length;bar.max=job?.source_ids.length||1;bar.value=job?.done_ids.length||0;
  $('prepare-course').textContent=job?'继续收集 / 重试失败':'获取并准备课程';
  renderCourseFiles(course,result.files||[]);
  if(job&&['queued','running'].includes(job.status)||(result.files||[]).some(file=>['upload','url'].includes(file.origin_kind)&&['discovered','downloaded','processing'].includes(file.status)))courseStatusTimer=setTimeout(showCourseStatus,2500);
  else if(c.questions!==course.questions&&!state.busy)await refreshBootstrap();
 }catch(error){if(request===courseStatusRequest)notice(error.message,true);}
}
async function prepareCourse(update=false){
 if(state.busy||courseLaunching)return;
 const limit=$('course-limit').value;
 courseLaunching=true;
 $('prepare-course').disabled=true;$('update-course').disabled=true;
 try{
  let course=selectedPreparationCourse();
  if(!course){
   if(!await runJob('/api/course-catalog',{}))return;
   course=selectedPreparationCourse();
  }
  if(!course){notice('公开目录未收录该课程，请核对课程名；当前仅支持 FDU-Sharing 来源。',true);return;}
  const job=await api('/api/prepare-course',{course:course.id,session:state.session,update,max_files:limit?Number(limit):null});
  courseNameEdited=false;
  notice(`${course.name}准备任务 ${job.job_id} 已${job.reused?'在运行':'启动'}，可以继续聊天。${job.warning||''}`);
  await refreshBootstrap();await refreshHistory();await showCourseStatus();
 }catch(error){notice(error.message,true);}
 finally{courseLaunching=false;$('prepare-course').disabled=state.busy;$('update-course').disabled=state.busy;}
}

function uploadCourseFiles(data){
 return new Promise((resolve,reject)=>{
  const request=new XMLHttpRequest();request.open('POST','/api/course-upload');
  request.upload.onprogress=event=>{if(event.lengthComputable){const percent=Math.round(event.loaded/event.total*100);$('course-upload-progress').value=percent;$('course-supplement-status').textContent=percent<100?`正在上传 ${percent}%`:'上传完成，正在保存原件…';}};
  request.onload=()=>{try{const result=JSON.parse(request.responseText);if(request.status<200||request.status>=300)reject(new Error(result.error||'上传失败'));else resolve(result);}catch{reject(new Error('上传响应无效，请刷新收集进度核对是否已保存。'));}};
  request.onerror=()=>reject(new Error('上传连接失败，请检查本地服务并刷新进度核对已保存资料。'));
  request.send(data);
 });
}
async function supplementCourse(kind,urls,courseName){
 if(state.busy||courseLaunching)return;
 const course=courseName||$('course-name').value.trim();
 if(!course){notice('请先填写课程名称。',true);$('course-name').focus();return;}
 const replace=$('course-supplement-replace').checked;
 const files=[...($('course-upload-files').files||[])];
 urls=urls||$('course-urls').value.split(/\r?\n/).map(url=>url.trim()).filter(Boolean);
 if(kind==='urls'&&(!urls.length||urls.length>20)){notice('请填写 1–20 个网址，每行一个。',true);return;}
 if(kind==='files'&&(!files.length||files.length>20||files.reduce((sum,file)=>sum+file.size,0)>100*1024*1024)){notice('请选择 1–20 个文件，合计不超过 100 MiB。',true);return;}
 courseLaunching=true;setBusy(true);$('progress-text').textContent='保存补充资料';$('course-supplement-status').textContent='正在提交…';$('course-upload-progress').hidden=kind!=='files';$('course-upload-progress').value=0;
 try{
  let result;
  if(kind==='files'){
   const data=new FormData();data.append('course',course);if(state.session)data.append('session',state.session);data.append('replace',String(replace));for(const file of files)data.append('files',file,file.name);
   result=await uploadCourseFiles(data);
  }else result=await api('/api/course-urls',{course,urls,replace,session:state.session});
  $('course-name').value=result.course.name;courseNameEdited=false;closeCoursePicker();
  const added=result.items.filter(item=>!item.reused&&!item.error).length,reused=result.items.filter(item=>item.reused).length;
  const message=`${result.course.name}：已添加 ${added} 份，跳过重复 ${reused} 份。${result.job?.queued?'新资料已排队，当前任务结束后继续处理。':result.job?'后台处理已启动，可以继续聊天。':''}${result.warning||''}`;
  $('course-supplement-status').textContent=message+'\n'+result.items.map(item=>`${item.name}：${item.error|| (item.reused?'已存在，未替换':'已保存来源')}`).join('\n');
  notice(message,Boolean(result.warning||result.items.some(item=>item.error)));
  if(kind==='files'){$('course-upload-files').value='';$('course-upload-selection').textContent='文件已提交，可在下方查看处理结果。';}else if(!urls.length||urls.join('\n')===$('course-urls').value.trim())$('course-urls').value='';
  $('course-supplement-replace').checked=false;
  setBusy(false);await refreshBootstrap();if(state.session)await refreshHistory();await showCourseStatus();
 }catch(error){$('course-supplement-status').textContent=error.message;notice(error.message,true);}
 finally{courseLaunching=false;setBusy(false);$('course-upload-progress').hidden=true;}
}
$('course-supplement-form').onsubmit=event=>{event.preventDefault();return supplementCourse('urls');};
$('upload-course-files').onclick=()=>supplementCourse('files');
$('course-upload-files').onchange=()=>{const files=[...$('course-upload-files').files];$('course-upload-selection').textContent=files.length?`${files.length} 个文件 · ${(files.reduce((sum,file)=>sum+file.size,0)/1024/1024).toFixed(2)} MiB\n`+files.map(file=>file.name).join('、'):'每批最多 20 个文件、合计 100 MiB。';};
$('course-form').onsubmit=event=>{event.preventDefault();return prepareCourse(false);};
$('update-course').onclick=()=>prepareCourse(true);
$('course-status').onclick=showCourseStatus;
$('course-name').oninput=()=>{courseInputChanged();openCoursePicker($('course-name').value);};
$('course-name').onfocus=()=>openCoursePicker();
$('course-name').onkeydown=event=>{
 if(event.isComposing)return;
 if(event.key==='ArrowDown'){event.preventDefault();if($('course-picker-results').hidden)openCoursePicker();$('available-course-names').querySelector('button:not(:disabled)')?.focus();}
 else if(event.key==='Escape'){event.preventDefault();closeCoursePicker();}
 else if(event.key==='Enter'&&!$('course-picker-results').hidden){event.preventDefault();const matches=(state.bootstrap?.question_courses||[]).filter(c=>c.name.toLocaleLowerCase().includes($('course-name').value.trim().toLocaleLowerCase()));if(matches.length===1){$('course-name').value=matches[0].name;courseInputChanged();showCourseStatus();}closeCoursePicker();}
};
$('browse-courses').onclick=()=>{if($('course-picker-results').hidden)openCoursePicker();else closeCoursePicker();};
$('course-picker').onfocusout=event=>{if(!$('course-picker').contains(event.relatedTarget))closeCoursePicker();};
$('course-name').onchange=showCourseStatus;
$('refresh-course-catalog').onclick=async()=>{if(await runJob('/api/course-catalog',{})){renderCourseCatalog();openCoursePicker();notice(`已读取 ${state.bootstrap.question_courses.length} 门公开课程，可输入课程名后准备。`);}};
$('open-course-library').onclick=()=>openMaterials('course');
$('course-start-search').onclick=async()=>{const course=selectedPreparationCourse();if(!course||state.busy)return;await setQuestionScope(course.id);await toggleSettings(false);$('message').focus();notice(`已选择 ${course.name}，请输入要找的知识点或题型。`);};
$('course-model-settings').onclick=async()=>{if(state.busy)return;await toggleSettings(true);$('question-model-settings').scrollIntoView({block:'start'});};
if(state.bootstrap)renderCourseCatalog();
