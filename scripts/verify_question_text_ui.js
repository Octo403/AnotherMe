const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const controls=new Map();
function node(tag,text=''){
 const value={tagName:tag,textContent:text,children:[],attrs:{},hidden:false,
  append(...children){this.children.push(...children);},replaceChildren(...children){this.children=[...children];},
  setAttribute(key,value){this.attrs[key]=value;},focus(){},querySelectorAll(){return [];},
  classList:{toggle(){},add(){}},get childElementCount(){return this.children.length;}};
 return value;
}
for(const match of fs.readFileSync('src/aitown/ui/static/index.html','utf8').matchAll(/id="([^"]+)"/g))controls.set(match[1],node('div'));
const document={getElementById:id=>controls.get(id),createElement:node,createTextNode:text=>node('#text',text),querySelectorAll:()=>[]};
const counts={settings:0,maintenance:0,course:0};
const sandbox={document,console,URLSearchParams,location:{search:''},
 refreshModelSettings:async()=>counts.settings++,showCourseStatus:async()=>counts.course++};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('src/aitown/ui/static/question_text.js','utf8'),sandbox);
vm.runInContext(fs.readFileSync('src/aitown/ui/static/app.js','utf8').replace(/^boot\(\);$/m,''),sandbox);
vm.runInContext('refreshMaintenance=async()=>{};',sandbox);
function flatten(value){return [value,...value.children.flatMap(flatten)];}
function text(value){return flatten(value).map(v=>v.textContent||'').join('\n');}
async function main(){
 const checks={};
 const markdown='## 1. 残差网络\n\n围绕 **ResNet** 完成以下内容：\n\n1. 绘制残差块；\n2. 计算参数量。\n\n### a) 限制\n保留公式 $x^2$。\n\n$$\ny=x+F(x)\n$$';
 const rendered=sandbox.renderQuestionText(markdown,'markdown');
 assert.equal(flatten(rendered).filter(n=>n.tagName==='ol').length,1);
 assert.equal(flatten(rendered).filter(n=>n.tagName==='li').length,2);
 assert(flatten(rendered).some(n=>n.tagName==='strong'&&n.textContent==='ResNet'));
 assert(flatten(rendered).some(n=>n.tagName==='h5'&&text(n).includes('a) 限制')));
 assert(text(rendered).includes('y=x+F(x)')&&text(rendered).includes('$x^2$'));
 assert(text(sandbox.renderQuestionText('保留乘法 $a*b*c$ 与 \\(a*b*c\\)。','markdown')).includes('$a*b*c$'));
 checks.markdown_headings_subparts_emphasis_and_formula_source_preserved=true;
 const malicious=sandbox.renderQuestionText('<script>alert(1)</script>\n\n[危险链接](javascript:alert)\n\n![图片](https://example.org/track.png)','markdown');
 assert(!flatten(malicious).some(n=>['script','img','a'].includes(n.tagName)));
 assert(text(malicious).includes('<script>'));
 checks.source_html_and_remote_media_do_not_execute_or_load=true;
 const q={id:1,source_id:1,path:'paper.md',url:'https://example.org/paper',number:'1',year:2026,exam:'final',category:'recollection',topics:[],images:[],missing:[],text:markdown,content_format:'markdown',origins:[]};
 const cards=sandbox.questionCards([q]);assert(text(cards).includes('计算参数量'));assert(!text(cards).includes('原题截图'));
 checks.markdown_card_displays_standard_text_without_missing_screenshot_warning=true;
 const local=sandbox.questionCards([{...q,url:'',origins:[{source_id:1,path:'paper.md',number:'1',file_kind:'raw',url:''}]}]);
 assert(flatten(local).some(n=>n.href==='/api/question-source?id=1&source=1')&&!flatten(local).some(n=>n.href===''));
 const lost=sandbox.questionCards([{...q,url:'',origins:[{source_id:1,path:'paper.md',number:'1',file_kind:'remote',url:''}]}]);
 assert(text(lost).includes('本地原件已丢失')&&!flatten(lost).some(n=>n.tagName==='a'));
 checks.uploaded_originals_link_locally_and_missing_files_do_not_get_fake_urls=true;
 const plain=sandbox.questionCards([{...q,content_format:'plain',text:'一、证明 **原文**\n保留换行。'}]);
 assert(text(plain).includes('**原文**')&&!flatten(plain).some(n=>n.tagName==='strong'));
 const image=sandbox.questionCards([{...q,content_format:'image',text:'OCR不得显示'}]);
 assert(!text(image).includes('OCR不得显示')&&text(image).includes('原题截图'));
 checks.plain_text_and_pdf_word_display_policies_remain_distinct=true;
 await sandbox.openMaterials('course');assert.equal(controls.get('settings').hidden,true);assert.equal(controls.get('materials').hidden,false);
 assert.equal(controls.get('personal-library-view').hidden,true);assert.equal(counts.course,1);assert.equal(counts.settings,0);
 await sandbox.toggleSettings(true);assert.equal(controls.get('materials').hidden,true);assert.equal(counts.settings,1);
 await sandbox.openMaterials('personal');assert.equal(controls.get('settings').hidden,true);assert.equal(controls.get('course-library-view').hidden,true);
 await controls.get('close-materials').onclick();assert(controls.get('settings').hidden&&controls.get('materials').hidden);
 checks.course_personal_materials_and_settings_switch_independently=true;
 console.log(JSON.stringify({checks,passed:true,note:'真实 Markdown/card/panel JS 与 DOM fixture，不替代浏览器视觉验收。'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
