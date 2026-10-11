// A small DOM-only Markdown renderer. Source HTML stays literal; no remote media loads.
function appendQuestionInline(node,text){
 const pattern=/(\$[^$\n]+\$|\\\([^\n]*?\\\)|`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|!?\[[^\]\n]+\]\([^\s)]+\))/g;
 let start=0;
 for(const match of text.matchAll(pattern)){
  if(match.index>start)node.append(document.createTextNode(text.slice(start,match.index)));
  const token=match[0];let child;
  if(token.startsWith('$')||token.startsWith('\\('))child=el('code',token,'question-inline-math');
  else if(token.startsWith('`'))child=el('code',token.slice(1,-1));
  else if(token.startsWith('**'))child=el('strong',token.slice(2,-2));
  else if(token.startsWith('*'))child=el('em',token.slice(1,-1));
  else {
   const link=token.match(/^(!?)\[([^\]]+)\]\(([^)]+)\)$/);
   if(!link[1]&&/^https?:\/\//i.test(link[3])){child=el('a',link[2]);child.href=link[3];child.target='_blank';child.rel='noopener noreferrer';}
   else child=document.createTextNode(token);
  }
  node.append(child);start=match.index+token.length;
 }
 if(start<text.length)node.append(document.createTextNode(text.slice(start)));
}
function renderQuestionText(text,format){
 const body=el('div',undefined,'question-text');
 if(format!=='markdown'){body.append(el('p',text));return body;}
 let paragraph=[],lists=[],fence=null,code=[],math=null;
 const flush=()=>{if(paragraph.length){const p=el('p');appendQuestionInline(p,paragraph.join('\n'));body.append(p);paragraph=[];}};
 const endLists=()=>{lists=[];};
 for(const line of text.split(/\r?\n/)){
  const marker=line.match(/^\s{0,3}(`{3,}|~{3,})/);
  if(fence){
   if(marker&&marker[1][0]===fence[0]&&marker[1].length>=fence.length){body.append(el('pre',code.join('\n'),'question-code'));fence=null;code=[];}
   else code.push(line);
   continue;
  }
  if(marker){flush();endLists();fence=marker[1];continue;}
  if(math!==null){if(line.trim()==='$$'){body.append(el('pre',math.join('\n'),'question-math'));math=null;}else math.push(line);continue;}
  if(line.trim()==='$$'){flush();endLists();math=[];continue;}
  if(!line.trim()){flush();continue;}
  const heading=line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
  if(heading){flush();endLists();const h=el(heading[1].length<=2?'h4':'h5');appendQuestionInline(h,heading[2]);body.append(h);continue;}
  const item=line.match(/^(\s*)(?:(\d+)[.)、]|([-+*]))\s+(.+)$/);
  if(item){
   flush();const depth=item[1].length,kind=item[2]?'ol':'ul';
   while(lists.length&&lists.at(-1).depth>depth)lists.pop();
   if(lists.length&&lists.at(-1).depth===depth&&lists.at(-1).kind!==kind)lists.pop();
   if(!lists.length||lists.at(-1).depth<depth){const list=el(kind);if(kind==='ol')list.start=Number(item[2]);const parent=lists.at(-1)?.last||body;parent.append(list);lists.push({depth,kind,list,last:null});}
   const li=el('li');if(kind==='ol')li.value=Number(item[2]);appendQuestionInline(li,item[4]);lists.at(-1).list.append(li);lists.at(-1).last=li;continue;
  }
  endLists();
  if(/^\s*>\s?/.test(line)){flush();const quote=el('blockquote');appendQuestionInline(quote,line.replace(/^\s*>\s?/,''));body.append(quote);continue;}
  if(/^\s*(?:---+|\*\*\*+)\s*$/.test(line)){flush();body.append(el('hr'));continue;}
  paragraph.push(line);
 }
 flush();
 if(fence)body.append(el('pre',code.join('\n'),'question-code'));
 if(math!==null)body.append(el('pre','$$\n'+math.join('\n'),'question-math'));
 return body;
}
