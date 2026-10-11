const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const controls=new Map();
let focused=null;
function node(tag,text=''){return {tagName:tag,textContent:text,value:'',children:[],disabled:false,attrs:{},
 append(...children){this.children.push(...children);},replaceChildren(...children){this.children=[...children];},scrollIntoView(){},focus(){focused=this;},setAttribute(key,value){this.attrs[key]=value;},querySelector(){return this.children.find(c=>c.tagName==='button'&&!c.disabled);},contains(child){return child===this||this.children.includes(child);}};}
for(const match of fs.readFileSync('src/aitown/ui/static/index.html','utf8').matchAll(/id="([^"]+)"/g))controls.set(match[1],node('div'));
const courses=[{id:'fdu-set-graph-theory',name:'集合与图论',files:18,questions:58},{id:'线性代数',name:'线性代数',files:48,questions:0}];
const state={bootstrap:{question_courses:courses},session:'one',history:{session:{}},busy:false};
const requests=[],notices=[],timers=new Map();let timerId=0,phase='running',failNext=false,hold=null;
const sandbox={state,console,$:id=>controls.get(id),el:node,notice:(text,error)=>notices.push({text,error}),
 openMaterials:async view=>{assert.equal(view,'course');controls.get('settings').hidden=true;controls.get('materials').hidden=false;await sandbox.showCourseStatus();},
 setTimeout:callback=>{timers.set(++timerId,callback);return timerId;},clearTimeout:id=>timers.delete(id),
 refreshBootstrap:async()=>{if(phase==='complete')courses[1].questions=2;sandbox.renderCourseCatalog();},refreshHistory:async()=>{},
 runJob:async(path)=>{assert.equal(path,'/api/course-catalog');if(!courses.some(c=>c.name==='数据结构'))courses.push({id:'数据结构',name:'数据结构',files:35,questions:0});return true;},
 api:async(path,payload)=>{
  requests.push({path,payload});
  if(failNext){failNext=false;throw Error('模拟启动失败');}
  if(path==='/api/prepare-course'){
   if(hold)return new Promise(resolve=>hold.resolve=resolve);
   return {job_id:7,reused:false,course:payload.course,warning:''};
  }
  assert(path.includes('course='+encodeURIComponent('线性代数')));
  const done=phase==='complete';
  return {status:done?'partial':'needs_preparation',coverage:{questions:done?2:0,discovered_files:48,states:{discovered:46,partial:done?2:0},raw_retained:0,raw_deleted:done?2:0},
   jobs:[{id:7,status:phase,stage:done?'finished':'parse',done_ids:done?[1,2]:[],source_ids:[1,2],current_source:done?null:1}],
   files:[{id:1,name:'真实卷名.doc',status:done?'partial':'processing',questions:done?2:0,local_available:done,file_kind:'original',url:'https://example.org/exam.doc'}]};
 }
};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('src/aitown/ui/static/course_prepare.js','utf8'),sandbox);
async function main(){
 const checks={};
 assert.equal(controls.get('available-course-names').children.length,2);
 controls.get('course-name').value='线性代数';controls.get('course-name').oninput();
 controls.get('course-limit').value='3';let prevented=false;
 await controls.get('course-form').onsubmit({preventDefault(){prevented=true;}});
 const first=requests.find(r=>r.path==='/api/prepare-course');
 assert(prevented);assert.equal(first.payload.course,'线性代数');assert.equal(first.payload.max_files,3);assert.equal(first.payload.session,'one');
 assert.equal(state.busy,false);assert(timers.size);assert(controls.get('course-progress').textContent.includes('线性代数'));
 checks.typed_course_submits_scope_and_limit_without_blocking_chat=true;
 const before=requests.length;state.busy=true;await sandbox.showCourseStatus();assert.equal(requests.length,before);assert(timers.size);
 state.busy=false;phase='complete';await [...timers.values()][0]();
 assert(controls.get('course-progress').textContent.includes('可检索 2 题'));assert(controls.get('course-catalog-note').textContent.includes('2 道题目'));
 checks.progress_defers_during_chat_and_refreshes_ready_counts=true;
 assert.equal(controls.get('course-progress-bar').value,2);assert.equal(controls.get('course-files').children.length,1);
 assert(controls.get('course-files').children[0].children.some(child=>child.href?.includes('/api/course-source?course=')));
 checks.completed_files_link_to_retained_local_attachments=true;
 await controls.get('open-course-library').onclick();assert.equal(controls.get('materials').hidden,false);assert.equal(controls.get('settings').hidden,true);
 checks.direct_course_library_entry_reads_persisted_progress=true;
 controls.get('course-name').value='未收录课程';controls.get('course-name').oninput();
 const previous=requests.length;await sandbox.prepareCourse();assert.equal(requests.length,previous);assert(notices.at(-1).error);
 assert.equal(controls.get('course-name').value,'未收录课程');
 checks.unknown_course_retains_name_and_requests_source=true;
 controls.get('course-name').value='线性代数';controls.get('course-name').oninput();
 failNext=true;await sandbox.prepareCourse();assert.equal(controls.get('prepare-course').disabled,false);assert.equal(controls.get('course-name').value,'线性代数');
 checks.failed_start_restores_controls_and_preserves_input=true;
 hold={};const launching=sandbox.prepareCourse();await sandbox.prepareCourse();
 assert(hold.resolve);const count=requests.filter(r=>r.path==='/api/prepare-course').length;
 hold.resolve({job_id:8,reused:false});hold=null;await launching;
 assert.equal(requests.filter(r=>r.path==='/api/prepare-course').length,count);
 checks.repeated_submit_during_launch_does_not_duplicate_request=true;
 await controls.get('refresh-course-catalog').onclick();assert.equal(controls.get('available-course-names').children.length,3);
 assert.equal(controls.get('course-name').value,'线性代数');
 checks.catalog_refresh_adds_courses_without_overwriting_input=true;
 // The entire directory is rendered in a bounded in-page list, including its last entry.
 for(let i=0;i<54;i++)courses.push({id:'课程'+i,name:'课程'+i,files:i,questions:0});
 sandbox.openCoursePicker();assert.equal(controls.get('available-course-names').children.length,57);
 assert(controls.get('course-catalog-results').textContent.includes('57 / 57'));
 const css=fs.readFileSync('src/aitown/ui/static/style.css','utf8');assert.match(css,/\.course-catalog-options\{[^}]*max-height:[^}]*overflow-y:auto/);
 controls.get('available-course-names').children.at(-1).onclick();
 assert.equal(controls.get('course-name').value,'课程53');assert.equal(controls.get('course-picker-results').hidden,true);
 checks.full_catalog_including_last_course_is_selectable_and_scroll_bounded=true;
 controls.get('course-name').value='线性';controls.get('course-name').oninput();
 assert.equal(controls.get('available-course-names').children.length,1);
 controls.get('course-name').onkeydown({key:'ArrowDown',preventDefault(){}});assert.equal(focused,controls.get('available-course-names').children[0]);
 controls.get('course-name').onkeydown({key:'Enter',preventDefault(){}});assert.equal(controls.get('course-name').value,'线性代数');
 sandbox.openCoursePicker();controls.get('course-name').onkeydown({key:'Escape',preventDefault(){}});assert.equal(controls.get('course-picker-results').hidden,true);
 checks.name_filter_keyboard_selection_and_escape_work=true;
 console.log(JSON.stringify({checks,passed:true,note:'真实课程表单 JS 事件与 DOM fixture；不替代浏览器视觉验收。'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
