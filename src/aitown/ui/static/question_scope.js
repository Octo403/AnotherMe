// A persistent course scope belongs to one conversation, separate from personal scope.
let questionMentionRange=null;
function closeQuestionMentions(){
 $('question-course-options').hidden=true;
 $('choose-question-course').setAttribute('aria-expanded','false');
 questionMentionRange=null;
}
function renderQuestionScope(){
 const entry=state.history?.session;
 const course=state.bootstrap?.question_courses?.find(item=>item.id===entry?.question_course);
 $('pinned-question-course').hidden=!course;
 $('question-course-label').textContent=course?'@'+course.name:'';
 $('question-scope-hint').textContent=course?'本会话只在此题库找题，点击 × 取消':'输入 @ 或点击“@ 题库”固定找题范围';
 $('message').placeholder=course?`在${course.name}中找题，可描述知识点、年份或题型`:'和 AnotherMe 聊聊，也可输入 @ 选择题库…';
 if(course)$('current-scope').textContent='找题范围：'+course.name;
 if(typeof syncPreparationCourse==='function')syncPreparationCourse(course);
 closeQuestionMentions();
}
async function setQuestionScope(course,range=null){
 if(state.busy||!state.session)return;
 const session=state.session;const message=$('message');const original=message.value;
 setBusy(true);notice('');
 try{
  const entry=await api('/api/question-scope',{session,course});
  if(state.session===session){
   if(range&&message.value===original){
    message.value=original.slice(0,range.start)+original.slice(range.end);
    message.setSelectionRange(range.start,range.start);
   }
   if(state.history?.session)state.history.session=entry;
   await refreshHistory();
   notice(course?'已固定题库，本会话后续消息都在该题库查找。':'已取消固定题库，恢复自动选择资料范围。');
   message.focus();
  }
  await refreshBootstrap();
 }catch(error){notice(error.message,true);}
 finally{setBusy(false);}
}
function showQuestionMentions(filter='',range=null){
 if(state.busy)return;
 const box=$('question-course-options');box.replaceChildren();questionMentionRange=range;
 const courses=(state.bootstrap?.question_courses||[]).filter(course=>course.name.includes(filter));
 for(const course of courses){
  const button=el('button','@'+course.name,'course-mention-option');
  button.type='button';button.disabled=state.busy;
  button.append(el('small',course.questions?`已整理 ${course.questions} 道题 · 固定当前会话范围`:`${course.files||0} 份公开资料 · 固定后首次找题时启动准备`));
  button.onclick=()=>setQuestionScope(course.id,questionMentionRange);
  button.onkeydown=event=>{
   if(event.key==='Escape'){event.preventDefault();closeQuestionMentions();$('message').focus();}
  };
  box.append(button);
 }
 if(!courses.length)box.append(el('p','没有这个题库，请选择已提供的题库。','muted'));
 box.hidden=false;$('choose-question-course').setAttribute('aria-expanded','true');
}
function updateQuestionMentions(){
 const message=$('message');const end=message.selectionStart;
 const before=message.value.slice(0,end);
 const match=/(?:^|\s)[@＠]([^\s@＠，。！？!?]*)$/.exec(before);
 if(!match){closeQuestionMentions();return;}
 const start=end-match[1].length-1;
 showQuestionMentions(match[1],{start,end});
}
function handleQuestionMentionKey(event){
 if(event.isComposing||$('question-course-options').hidden)return false;
 if(event.key==='Escape'){event.preventDefault();closeQuestionMentions();return true;}
 const option=$('question-course-options').querySelector('button');
 if(option&&event.key==='ArrowDown'){event.preventDefault();option.focus();return true;}
 if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();if(option)option.click();return true;}
 return false;
}
$('message').oninput=updateQuestionMentions;
$('choose-question-course').onclick=()=>{
 if($('question-course-options').hidden)showQuestionMentions();else closeQuestionMentions();
};
$('clear-question-course').onclick=()=>setQuestionScope('');
