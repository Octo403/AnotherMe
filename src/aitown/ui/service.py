"""All Agent/SQLite work runs on one worker; HTTP polling reads job state only."""
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from ..errors import AgentError
from ..graph import AnotherMeAgent
from ..models import Reply
from ..nodes import rag
from ..runtime import UserStatement
from ..style import ProfileStore
from .model_settings import ModelPreferences, api_operation


class UIError(ValueError):
    def __init__(self,message,status=400):
        super().__init__(message);self.status=status


class ChatInput(BaseModel):
    session: str = Field(min_length=1,max_length=100)
    question: str = Field(min_length=1,max_length=6000)
    scene: str | None = None


class ObservedAgent(AnotherMeAgent):
    def __init__(self,settings,progress):
        self.progress=progress
        super().__init__(settings)
    def context(self,state):
        self.progress('读取会话');return super().context(state)
    def intent(self,state):
        self.progress('理解问题');return super().intent(state)
    def rag(self,state):
        self.progress('检索资料');return super().rag(state)
    def skill(self,state):
        names=[call.name for call in state['decision'].calls]
        self.progress('本地召回与 ChatGPT 筛题' if 'question_search' in names and self.question_bank.config.similar_provider=='codex_chatgpt'
            else '整理资料' if 'dialogue_summary' in names else '执行技能')
        return super().skill(state)
    def reflect(self,state):
        self.progress('生成与核对' if self.model else '整理离线结果')
        return super().reflect(state)


class UIService:
    def __init__(self,settings,metadata_path,*,data='synthetic'):
        self.settings=settings;self.data=data;self.metadata_path=Path(metadata_path)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='anotherme-ui')
        self.lock=threading.Lock();self.jobs={};self.active=None
        try:self.pool.submit(self._start).result()
        except Exception:self.pool.shutdown();raise

    def _start(self):
        self.metadata_path.parent.mkdir(parents=True,exist_ok=True)
        self.meta=sqlite3.connect(self.metadata_path);self.meta.row_factory=sqlite3.Row
        self.meta.executescript('''
            CREATE TABLE IF NOT EXISTS ui_sessions(agent_id TEXT NOT NULL,session_id TEXT NOT NULL,title TEXT NOT NULL,scope TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(agent_id,session_id));
            CREATE TABLE IF NOT EXISTS ui_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ui_skills(name TEXT PRIMARY KEY,enabled INTEGER NOT NULL);
        ''')
        if 'question_course' not in {row[1] for row in self.meta.execute('PRAGMA table_info(ui_sessions)')}:
            with self.meta:self.meta.execute("ALTER TABLE ui_sessions ADD COLUMN question_course TEXT NOT NULL DEFAULT ''")
        self.model_preferences=ModelPreferences(search_mode=self.settings.question_similar_provider,
            api=self.settings.question_api,codex_model=self.settings.question_similar_model,
            codex_images=self.settings.question_similar_images,extractor=self.settings.question_extractor,
            codex_path=self.settings.question_codex_path,
            mineru_model=self.settings.mineru_api_model,mineru_token=self.settings.mineru_api_key)
        self.model_settings_warning=''
        saved=self.meta.execute("SELECT value FROM ui_settings WHERE key='model_preferences'").fetchone()
        if saved:
            try:self.model_preferences=ModelPreferences.restore(saved[0])
            except (ValueError,ValidationError):
                self.model_preferences=ModelPreferences()
                self.model_settings_warning='加密模型配置无法读取，请在设置中重新填写 Key。'
        self._apply_model_preferences()
        if self.settings.mode=='live' and not self.live_available():raise UIError('界面的真实模型模式仅支持已配置的本机服务。')
        self.agent=ObservedAgent(self.settings,self._progress)
        for row in self.meta.execute('SELECT * FROM ui_skills'):
            if row['name'] in self.agent.skills._skills:self.agent.skills.enable(row['name'],bool(row['enabled']))

    def live_available(self):
        return bool(self.settings.model and self.settings.api_key and urlparse(self.settings.base_url).hostname in ('127.0.0.1','localhost','::1'))

    def _progress(self,phase):
        with self.lock:
            if self.active:self.jobs[self.active]['phase']=phase

    def call(self,method,*args):
        with self.lock:
            if self.active:raise UIError('AnotherMe 正在处理上一项操作，请稍后再试。',409)
        return self.pool.submit(getattr(self,method),*args).result()

    def start_job(self,method,*args):
        with self.lock:
            if self.active:raise UIError('AnotherMe 正在处理上一项操作，请稍后再试。',409)
            key=str(uuid.uuid4());self.active=key
            self.jobs[key]={'id':key,'status':'running','phase':'准备处理','started':time.time()}
        self.pool.submit(self._run,key,method,args)
        return {'job_id':key}

    def _run(self,key,method,args):
        try:
            result=getattr(self,method)(*args)
            update={'status':'complete','phase':'完成','result':result}
        except (ValueError,KeyError,ValidationError) as exc:
            message=str(exc) if not isinstance(exc,ValidationError) else '输入格式不正确，请检查字段。'
            update={'status':'failed','phase':'处理失败','error':message[:500]}
        except AgentError as exc:
            update={'status':'failed','phase':'处理失败','error':exc.info.message,'recoverable':True}
        except Exception as exc:
            update={'status':'failed','phase':'处理失败','error':f'操作失败（{type(exc).__name__}），请检查配置或恢复会话。','recoverable':True}
        with self.lock:
            self.jobs[key].update(update,finished=time.time());self.active=None
            # Completed response bodies remain in SQLite, not an unbounded cache.
            completed=[k for k,v in self.jobs.items() if v['status']!='running']
            for old in completed[:-50]:self.jobs.pop(old,None)

    def job(self,key):
        with self.lock:
            if key not in self.jobs:raise UIError('任务不存在或已清理，请刷新会话。',404)
            return dict(self.jobs[key])

    def _session(self,session):
        row=self.meta.execute('SELECT * FROM ui_sessions WHERE agent_id=? AND session_id=?',(self.settings.agent.id,session)).fetchone()
        if row is None:raise UIError('会话不存在，请从会话列表选择。',404)
        return dict(row)

    def _scopes(self):
        repo=self.agent.retrieval_repository or self.agent.runtime_repository
        scopes={row[0] for row in repo.conn.execute('SELECT DISTINCT scope FROM memories')}
        scopes|={row[0] for row in repo.conn.execute('SELECT DISTINCT scope FROM source_records')}
        if self.agent.style_repository:
            scopes|={row[0] for row in self.agent.style_repository.conn.execute('SELECT DISTINCT conversation_id FROM episodes')}
        if self.settings.style_scope:scopes.add(self.settings.style_scope)
        return sorted(scope for scope in scopes if scope)

    def _bootstrap(self):
        # Existing CLI sessions are visible; unknown scopes stay unset, never guessed.
        for row in self.agent.memory.conn.execute('SELECT session_id FROM runtime_sessions WHERE agent_id=?',(self.settings.agent.id,)).fetchall():
            if row[0]=='fixture' and self.data=='synthetic':continue
            with self.meta:self.meta.execute('INSERT OR IGNORE INTO ui_sessions(agent_id,session_id,title,scope,created) VALUES(?,?,?,?,?)',
                (self.settings.agent.id,row[0],row[0],'',time.time()))
        sessions=[dict(row) for row in self.meta.execute('SELECT session_id AS id,title,scope,created,question_course FROM ui_sessions WHERE agent_id=? ORDER BY created DESC',(self.settings.agent.id,))]
        return {'name':self.settings.agent.name,'mode':self.settings.mode,'data':self.data,
                'data_label':{'synthetic':'合成演示资料','wechat':'微信资料副本','configured':'已配置的本地资料'}.get(self.data,'本地资料'),
                'live_available':self.live_available(),
                'model':self.settings.model if self.live_available() else '', 'sessions':sessions,'scopes':self._scopes(),
                'default_scope':self.settings.style_scope,'skills':self.agent.skills.catalog(include_disabled=True),
                'retrieval_error':self.agent.retrieval_error,
                'question_courses':self.agent.question_bank.courses(),
                'question_bank_directory':str(self.agent.question_bank.config.root.resolve()),
                'question_model':self.model_preferences.search_mode,
                'setup_required':bool(__import__('os').getenv('AITOWN_PORTABLE_HOME')) and not self.meta.execute("SELECT 1 FROM ui_settings WHERE key='setup_complete'").fetchone(),
                'question_model_name':self.model_preferences.api.model if self.model_preferences.search_mode=='api' else self.model_preferences.codex_model}

    def _apply_model_preferences(self):
        preferences=self.model_preferences
        self.settings=self.settings.model_copy(update={
            'question_similar_provider':preferences.search_mode,'question_api':preferences.api,
            'question_similar_model':preferences.codex_model,'question_similar_images':preferences.codex_images,
            'question_codex_path':preferences.codex_path,
            'question_extractor':preferences.extractor,'mineru_api_key':preferences.mineru_token,
            'mineru_api_model':preferences.mineru_model})
        if hasattr(self,'agent'):
            self.agent.settings=self.settings
            self.agent.question_bank.config=self.agent.question_bank.config.model_copy(update={
                'similar_provider':preferences.search_mode,'api':preferences.api,
                'similar_model':preferences.codex_model,'similar_images':preferences.codex_images,
                'codex_path':preferences.codex_path,
                'extractor':preferences.extractor,'mineru_api_key':preferences.mineru_token,
                'mineru_api_model':preferences.mineru_model})
            self.agent.question_bank.similarity=None

    def _model_settings(self):
        from ..question_bank.repository import BankRepository
        from ..question_bank.mineru_api import validate_source
        sources=[]
        if self.agent.question_bank.config.database.is_file():
            with BankRepository(self.agent.question_bank.config.database,readonly=True) as repo:
                for source in map(dict,repo.conn.execute('SELECT * FROM sources ORDER BY course_id,path,member')):
                    try:validate_source(source)
                    except ValueError:continue
                    if not source['remote_removed']:
                        sources.append({'id':source['id'],'name':source['path'].split('/')[2]+' · '+(source['member'] or source['path'].split('/')[-1])})
        from ..question_bank.word import libreoffice_command
        return {**self.model_preferences.public(),'warning':self.model_settings_warning,'sample_sources':sources,
                'documents':{'libreoffice':bool(libreoffice_command()),'pdf':True,'docx_text':True,
                    'directory':str(self.agent.question_bank.config.root.resolve())}}

    def _setup_complete(self):
        with self.meta:
            self.meta.execute("INSERT OR REPLACE INTO ui_settings VALUES('setup_complete','1')")
        return {'complete':True}

    def _resolve_model_settings(self,payload):
        candidate=ModelPreferences.model_validate(payload)
        old=self.model_preferences
        if not candidate.api.api_key and not candidate.clear_api_key and (
            candidate.api.base_url.rstrip('/')==old.api.base_url.rstrip('/') and candidate.api.provider==old.api.provider):
            candidate.api.api_key=old.api.api_key
        if not candidate.mineru_token and not candidate.clear_mineru_token:
            candidate.mineru_token=old.mineru_token
        if candidate.clear_api_key:candidate.api.api_key=''
        if candidate.clear_mineru_token:candidate.mineru_token=''
        return candidate

    def _save_model_settings(self,payload):
        candidate=self._resolve_model_settings(payload)
        if candidate.clear_api_key and not candidate.api.ready() and candidate.search_mode=='api':
            candidate.search_mode='none'
        if candidate.clear_mineru_token and candidate.extractor=='mineru_api':
            candidate.extractor='basic'
        if candidate.search_mode=='api' and not candidate.api.ready():
            raise UIError('在线筛题需要具体模型型号和 API Key；本机无鉴权接口可留空 Key。')
        if candidate.extractor=='mineru_api' and not candidate.mineru_token:
            raise UIError('MinerU 在线解析需要填写 Token。')
        stored=candidate.stored()
        with self.meta:self.meta.execute("INSERT INTO ui_settings VALUES('model_preferences',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(stored,))
        self.model_preferences=candidate;self.model_settings_warning=''
        self._apply_model_preferences()
        return self._model_settings()

    def _test_model_settings(self,payload):
        candidate=self._resolve_model_settings(payload)
        return api_operation(candidate.api,'test')

    def _model_catalog(self,payload):
        candidate=self._resolve_model_settings(payload)
        if candidate.search_mode=='codex_chatgpt':
            from ..question_bank.codex_catalog import codex_model_catalog
            return codex_model_catalog(cli_path=candidate.codex_path) if candidate.codex_path else codex_model_catalog()
        return api_operation(candidate.api,'models')

    def _mineru_sample(self,payload):
        from ..question_bank.repository import BankRepository
        from ..question_bank.mineru_api import parse_mineru_api_pdf
        if not self.model_preferences.mineru_token:
            raise UIError('请先保存 MinerU Token，再试解析一份公开原卷。')
        config=self.agent.question_bank.config
        with BankRepository(config.database,readonly=True) as repo:
            source=repo.source(int(payload.get('source_id',0)))
        output=config.root/'experiments'/'mineru-api'/str(uuid.uuid4())
        self._progress('MinerU 正在解析公开原卷')
        parsed=parse_mineru_api_pdf(None,source,output,config)
        (output/'parsed.json').write_text(parsed.model_dump_json(indent=2),encoding='utf-8')
        return {'status':parsed.status,'questions':len(parsed.questions),'pages':len(parsed.pages),
            'images':sum(len(q.images) for q in parsed.questions),'notes':parsed.notes,
            'source':source['url'],'message':'小样本已解析并单独保存，未替换题库；请核对原图与公式后再更新课程。'}

    def _create_session(self,payload):
        scope=str(payload.get('scope',''))
        if scope and scope not in self._scopes():raise UIError('请选择已有的资料范围。')
        title=str(payload.get('title','新的对话')).strip()[:60] or '新的对话'
        session=str(uuid.uuid4());self.agent.memory.session(session,self.settings.agent.id)
        with self.meta:self.meta.execute('INSERT INTO ui_sessions(agent_id,session_id,title,scope,created) VALUES(?,?,?,?,?)',(self.settings.agent.id,session,title,scope,time.time()))
        return {'id':session,'title':title,'scope':scope,'question_course':''}

    def _question_scope(self,payload):
        entry=self._session(payload.get('session',''))
        course=payload.get('course','')
        if course:
            course=self.agent.question_bank.resolve_course(course)
            if not course:raise UIError('请选择已有的课程题库；未收录课程需先提供资料来源。')
        with self.meta:self.meta.execute('UPDATE ui_sessions SET question_course=? WHERE agent_id=? AND session_id=?',
            (course,self.settings.agent.id,entry['session_id']))
        return self._session(entry['session_id'])

    def _history(self,session):
        entry=self._session(session)
        rows=self.agent.memory.rows(session,self.settings.agent.id,recent=False)
        turns=[]
        for row in rows[-100:]:
            run=self.agent.memory.conn.execute('SELECT reply_json,scope FROM runtime_runs WHERE id=?',(row['request_id'],)).fetchone()
            reply=self._present(Reply.model_validate_json(run['reply_json']),run['scope']) if run and run['reply_json'] else None
            turns.append({'id':row['id'],'question':row['question'],'answer':reply['answer'] if reply else row['answer'],
                          'created':row['created'],'reply':reply,'feedback':[dict(r) for r in self.agent.memory.conn.execute('SELECT kind,correction FROM feedback WHERE turn_id=? ORDER BY id',(row['id'],))]})
        pending=self.agent.memory.pending(session,self.settings.agent.id)
        return {'session':entry,'turns':turns,'pending':bool(pending),'pending_question':pending['question'] if pending else None}

    def _present(self,reply,scope):
        state={'sources':reply.sources,'input':reply.original_question,'decision':reply.decision,'style_scope':scope,
               'personal_query':bool(reply.decision and reply.decision.purpose=='personal'),'agent':self.settings.agent}
        live,removed=rag.current_sources(self.agent,state) if reply.sources and reply.decision else (reply.sources,[])
        sources=[]
        repo=self.agent.retrieval_repository or self.agent.runtime_repository
        for source in live:
            evidence=[]
            for mid in source.source_ids:
                row=repo.conn.execute('SELECT id,content,timestamp,direction,speaker_id,conversation_id,review_status FROM message_refs WHERE id=?',(mid,)).fetchone()
                if row and row['review_status']=='approved' and (not scope or row['conversation_id']==scope):evidence.append(dict(row))
            for rid in source.source_record_ids:
                row=repo.conn.execute('SELECT id,source_kind,content,created AS timestamp,scope FROM source_records WHERE id=?',(rid,)).fetchone()
                if row and row['scope']==scope:evidence.append(dict(row))
            presented_source={**source.model_dump(mode='json'),'ref':source.ref,'evidence':evidence}
            if source.question_id is not None:
                presented_source['text']='原题内容请查看下方题目卡片或打开来源文件。'
            sources.append(presented_source)
        profile=[]
        if self.agent.style_repository and self.agent.style_profile and scope:
            active={f'trait:{c.trait_id}':c for c in ProfileStore(self.agent.style_repository).active(self.agent.style_profile,scope)}
            profile=[s.model_dump(mode='json') for s in reply.profile_evidence if s.source in active]
        generated=reply.mode=='live' and reply.stop_reason=='answered' and any(c.provider_request and c.phase=='review' and c.status=='ok' for c in reply.model_calls)
        output_kind='generated' if generated else 'offline' if reply.mode=='demo' else 'program_result'
        question_models=[call for skill in reply.skills if skill.ok and skill.name=='question_search'
            for call in skill.data.get('model_calls',[]) if call.get('status')=='ok']
        if question_models:output_kind='question_selected'
        skills=[] if removed else [s.model_dump(mode='json') for s in reply.skills]
        questions=[]
        question_texts=[]
        for skill in skills:
            if skill['ok'] and skill['name'] in ('question_search','question_get'):
                from ..question_bank.models import GetArgs
                saved=skill['data']['results']
                question_texts.extend(q['text'] for q in saved if q.get('text'))
                current=self.agent.question_bank.get(GetArgs(question_ids=[q['id'] for q in saved],
                    include_answer=any('answers' in q for q in saved))).results if saved else []
                previous={q['id']:q for q in saved}
                for question in current:
                    for key in ('score','match_reason'):
                        if key in previous.get(question['id'],{}):question[key]=previous[question['id']][key]
                skill['data']['results']=current
                skill['data']['question_ids']=[q['id'] for q in current]
                skill['data']['count']=len(current)
                for question in current:
                    text=question.get('text') if question.get('content_format') in ('markdown','plain') else question.pop('text',None)
                    if text:question_texts.append(text)
                    questions.append(question)
        def display_text(text):
            if text is None:return None
            for original in sorted(set(question_texts),key=len,reverse=True):
                text=text.replace(original,'原题内容请查看下方题目卡片或打开来源文件。')
            return text
        return {'request_id':reply.request_id,'answer':'此回复的来源已更新或删除，请重新提问。' if removed else display_text(reply.answer),
            'questions':questions,
            'output_kind':output_kind,'scope':scope,
            'mode':reply.mode,'stop_reason':reply.stop_reason,'sources':sources,'profile':profile,
            'style':reply.style_examples if not removed else [],'skills':skills,
            'warnings':['此回复的旧来源已移除。'] if removed else reply.warnings,
            'debug':{'trace':reply.trace,'decision':reply.decision.model_dump(mode='json') if reply.decision else None,
                     'rewritten_question':reply.rewritten_question,'references':[r.model_dump(mode='json') for r in reply.references],
                     'execution':[s.model_dump() for s in reply.execution],'errors':[e.model_dump() for e in reply.errors],
                     'model_calls':[c.model_dump() for c in reply.model_calls],'draft':None if removed else display_text(reply.draft_answer),
                     'reviews':[] if removed else [r.model_dump(mode='json') for r in reply.reviews]},'source_changed':bool(removed)}

    def _chat(self,payload):
        parsed=ChatInput.model_validate(payload);entry=self._session(parsed.session)
        if not parsed.question.strip():raise UIError('请输入消息。')
        if parsed.scene not in (None,'agree','refuse','comfort','explain','task','uncertain','chat'):raise UIError('回应场景不正确。')
        if self.agent.memory.pending(parsed.session,self.settings.agent.id):
            raise UIError('会话有未完成轮次，请先继续未完成的对话。')
        import re
        from ..question_bank.conversation import course_mention,named_course,mention_pattern
        courses=self.agent.question_bank.courses()
        mentioned=course_mention(parsed.question,courses)
        if mentioned:entry=self._question_scope({'session':parsed.session,'course':mentioned})
        named=named_course(parsed.question,courses)
        if named and re.search('题库|知识库|找.*题|准备|获取|下载|建立|新增|添加|加入|整理|更新|重试',parsed.question):
            entry=self._question_scope({'session':parsed.session,'course':named})
        selected=entry['question_course']
        content=mention_pattern(courses).sub('',parsed.question).strip(' ，,。')
        personal=re.search(r'微信|个人资料|记忆|我的(?:经历|偏好|计划|生日)|我(?:喜欢|不喜欢|去过|以前|昨天)|记住[：:]|记一下[：:]|纠正事实',content)
        if selected and content and not personal:
            status=self.agent.question_bank.status(selected)
            explicit=bool(re.search('准备|获取.*(?:题库|知识库|资料)|下载.*(?:题库|知识库|资料)|建立|新增|添加|加入|整理|更新|重试',content))
            if explicit or (status.status=='needs_preparation' and not status.jobs):
                self._prepare_course({'course':selected,'update':'更新' in content})
        reply=self.agent.ask(parsed.question.strip(),parsed.session,scope=entry['scope'],scene=parsed.scene,
            question_course=entry['question_course'])
        if entry['title']=='新的对话':
            with self.meta:self.meta.execute('UPDATE ui_sessions SET title=? WHERE agent_id=? AND session_id=?',(parsed.question.strip()[:24],self.settings.agent.id,parsed.session))
        return {'session':parsed.session,'reply':self._present(reply,entry['scope'])}

    def _course_status(self,course=None):
        from ..question_bank.models import COURSE_ID
        selected=self.agent.question_bank.resolve_course(course or COURSE_ID)
        if not selected:raise UIError('未收录该课程，请刷新课程目录或提供资料来源。')
        return self.agent.question_bank.status(selected).model_dump(mode='json')

    def _discover_courses(self,payload=None):
        from ..question_bank.sources import GithubSource
        from ..question_bank.repository import BankRepository
        remote=GithubSource(self.agent.question_bank.config)
        try:
            courses,sources=remote.discover()
        except ValueError as exc:
            raise UIError(str(exc)+' 原有课程目录和已收集资料均已保留；仍可上传文件或添加网址。') from None
        finally:
            remote.close()
        with BankRepository(self.agent.question_bank.config.database) as repo:
            repo.save_discovery(courses,sources)
        return {'courses':self.agent.question_bank.courses(),'files':len(sources)}

    def _prepare_course(self,payload):
        from ..question_bank.scheduler import launch
        from ..question_bank.models import COURSE_ID
        course=self.agent.question_bank.resolve_course(payload.get('course') or COURSE_ID)
        if not course:raise UIError('未收录该课程，请刷新课程目录；若仍未找到，需要提供资料来源。')
        session=payload.get('session')
        if session:self._session(session)
        limit=payload.get('max_files')
        if limit is not None and (type(limit) is not int or limit<1):raise UIError('处理份数须为正整数。')
        config=self.agent.question_bank.config
        result=launch(config,course,update=bool(payload.get('update',False)),max_files=limit)
        if session:self._question_scope({'session':session,'course':course})
        return {**result,'course':course,'warning':''}

    def _question_image(self,question_id,index):
        from ..question_bank.models import GetArgs
        result=self.agent.question_bank.get(GetArgs(question_ids=[question_id]))
        if not result.results or index<0 or index>=len(result.results[0]['images']):raise UIError('题目图片不存在。',404)
        return self._bank_image(result.results[0]['images'][index])

    def _supplement_course(self,payload,files=None):
        from ..question_bank.repository import BankRepository
        from ..question_bank.supplements import add_uploads,add_urls
        from ..question_bank.scheduler import launch
        session=payload.get('session')
        if session:self._session(session)
        replace=payload.get('replace',False)
        if type(replace) is not bool:raise UIError('替换资料选项须为布尔值。')
        config=self.agent.question_bank.config
        try:
            with BankRepository(config.database) as repo:
                if files is None:course,items=add_urls(repo,payload.get('course'),payload.get('urls'),replace=replace)
                else:course,items=add_uploads(repo,config,payload.get('course'),files,replace=replace)
        except ValueError as exc:raise UIError(str(exc)) from None
        ids=[item['id'] for item in items if not item['reused'] and not item.get('error')]
        job=None;warning=''
        if ids:
            try:job=launch(config,course['id'],source_ids=ids)
            except Exception as exc:warning=f'资料已保存，准备任务未启动（{type(exc).__name__}），请点击继续收集。'
        if session:self._question_scope({'session':session,'course':course['id']})
        return {'course':course,'items':items,'job':job,'warning':warning}

    def _retry_course_source(self,payload):
        from ..question_bank.repository import BankRepository
        from ..question_bank.scheduler import launch
        selected=self.agent.question_bank.resolve_course(payload.get('course'))
        if not selected:raise UIError('未收录该课程。',404)
        source_id=payload.get('source')
        if type(source_id) is not int:raise UIError('资料编号无效。')
        config=self.agent.question_bank.config
        with BankRepository(config.database) as repo:
            try:source=repo.source(source_id)
            except ValueError:raise UIError('课程资料不存在。',404) from None
            if source['course_id']!=selected:raise UIError('资料不属于当前课程。',404)
            if source['status']!='failed':raise UIError('仅失败资料可以在此重试；重新解析请使用更新课程资料。')
            if repo.conn.execute("SELECT 1 FROM jobs WHERE course_id=? AND status IN ('queued','running')",(selected,)).fetchone():
                raise UIError('课程任务正在运行，请结束后再重试此文件。',409)
            if source['repository']=='user:upload' and (not source['local_path'] or (source['error'] or '').startswith('保存失败')):
                raise UIError('原件未保存，请勾选替换后重新上传。')
        return launch(config,selected,source_ids=[source_id])

    def _question_page(self,question_id,page):
        from ..question_bank.models import GetArgs
        result=self.agent.question_bank.get(GetArgs(question_ids=[question_id]))
        pages=result.results[0]['source_pages'] if result.results else []
        original=next((p for p in pages if p['ordinal']==page and p['location_kind']=='page'),None)
        if not original or not original['image']:raise UIError('当前题目的原页图片不存在。',404)
        return self._bank_image(original['image'])

    def _question_source(self,question_id,source_id):
        from ..question_bank.models import GetArgs
        from ..question_bank.repository import BankRepository
        result=self.agent.question_bank.get(GetArgs(question_ids=[question_id]))
        allowed_sources={s['source_id'] for s in result.results[0]['origins']} if result.results else set()
        if result.results and source_id not in allowed_sources:
            answered=self.agent.question_bank.get(GetArgs(question_ids=[question_id],include_answer=True))
            allowed_sources.update(origin['source_id'] for answer in answered.results[0].get('answers',[]) for origin in answer['origins'])
        if source_id not in allowed_sources:
            raise UIError('当前题目来源不存在。',404)
        with BankRepository(self.agent.question_bank.config.database,readonly=True) as repo:
            source=repo.source(source_id)
        return self._source_file(source)

    def _course_source(self,course,source_id):
        from ..question_bank.repository import BankRepository
        selected=self.agent.question_bank.resolve_course(course)
        if not selected:raise UIError('未收录该课程。',404)
        with BankRepository(self.agent.question_bank.config.database,readonly=True) as repo:
            try:source=repo.source(source_id)
            except ValueError:raise UIError('课程资料不存在。',404) from None
        if source['course_id']!=selected:raise UIError('资料不属于当前课程。',404)
        return self._source_file(source)

    def _source_file(self,source):
        from ..question_bank.files import local_source_file
        try:target,_=local_source_file(self.agent.question_bank.config.root,source)
        except FileNotFoundError as exc:raise UIError(str(exc),404) from None
        mime={'.pdf':'application/pdf','.doc':'application/msword',
              '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}.get(target.suffix.lower(),'application/octet-stream')
        return target.read_bytes(),mime,target.name

    def _bank_image(self,path):
        target=Path(path).resolve()
        allowed=(self.settings.question_bank_dir/'processed').resolve()
        if not target.is_relative_to(allowed) or not target.is_file():raise UIError('题目图片不存在。',404)
        mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(target.suffix.lower())
        if not mime:raise UIError('当前图表格式不能在网页预览，请打开原文件。',415)
        return target.read_bytes(),mime

    def _resume(self,session):
        self._session(session)
        reply=self.agent.resume(session)
        run=self.agent.memory.conn.execute('SELECT scope FROM runtime_runs WHERE id=?',(reply.request_id,)).fetchone()
        return {'session':session,'reply':self._present(reply,run[0] if run else '')}

    def _turn(self,session,turn_id):
        self._session(session)
        row=self.agent.memory.source_turn(int(turn_id),session,self.settings.agent.id)
        if row is None:raise UIError('回复不在当前会话中或已过期。',404)
        return row

    def _feedback(self,payload):
        self._turn(payload.get('session',''),payload.get('turn_id',0))
        kind=payload.get('kind')
        if kind not in ('like_me','unlike_me','fact_wrong'):raise UIError('反馈类型不正确。')
        comment=str(payload.get('comment','')).strip()
        if len(comment)>2000:raise UIError('批注请控制在 2000 字以内。')
        fid=self.agent.runtime_repository.add_feedback(int(payload['turn_id']),kind,comment)
        return {'id':fid,'saved':True}

    def _facts(self,session):
        entry=self._session(session);repo=self.agent.retrieval_repository or self.agent.runtime_repository
        if not entry['scope']:return []
        return [{'id':f.id,'attribute':f.attribute,'value':f.value} for f in repo.query_facts('',subject='self',limit=100)
                if entry['scope'] in {s.conversation_id for s in repo.sources('fact',f.id)}]

    def _correct(self,payload):
        entry=self._session(payload.get('session',''));turn=self._turn(entry['session_id'],payload.get('turn_id',0))
        repo=self.agent.runtime_repository
        if self.agent.retrieval_repository and self.agent.retrieval_repository.path.resolve()!=repo.path.resolve():raise UIError('纠正需要运行库与检索库相同。')
        old=repo.conn.execute("SELECT * FROM facts WHERE id=? AND subject='self' AND status='active'",(int(payload.get('fact_id',0)),)).fetchone()
        if not old:raise UIError('请选择当前有效的本人事实。')
        statement=UserStatement(kind='user_correction',attribute=old['attribute'],value=str(payload.get('value','')).strip(),
            scope=entry['scope'],corrects_fact_id=old['id'])
        self._progress('保存纠正')
        with repo.conn:
            repo.conn.execute('BEGIN IMMEDIATE')
            fid=self.agent.memory._record_statement(statement,f'用户在界面明确纠正事实 {old["id"]}：{statement.value}',entry['session_id'],self.settings.agent.id)
            repo.conn.execute('INSERT INTO feedback(turn_id,kind,correction,created) VALUES(?,?,?,?)',(turn['id'],'fact_correction',f'事实 {old["id"]} → {fid}：{statement.value}',time.time()))
        warning=None
        if self.agent.retriever:
            try:self._progress('更新资料');self.agent.retriever.refresh('personal')
            except Exception:warning='纠正已保存；索引刷新失败，下次查询将重试。'
        return {'saved':True,'fact_id':fid,'warning':warning}

    def _maintenance(self,session):
        entry=self._session(session);repo=self.agent.retrieval_repository or self.agent.runtime_repository
        documents=[dict(row) for row in repo.conn.execute("SELECT d.id,d.name,length(d.content) AS chars FROM knowledge_documents d WHERE purpose='knowledge' AND NOT EXISTS(SELECT 1 FROM document_sources s WHERE s.document_id=d.id) ORDER BY d.id")]
        profile=[]
        if self.agent.style_repository and self.agent.style_profile and entry['scope']:
            profile=[c.model_dump(mode='json') for c in ProfileStore(self.agent.style_repository).active(self.agent.style_profile,entry['scope'])]
        memories=[]
        if entry['scope']:
            approved={row[0] for row in repo.conn.execute("SELECT id FROM message_refs WHERE conversation_id=? AND review_status='approved' AND status IN ('text','structured_text')",(entry['scope'],))}
            for row in repo.conn.execute("SELECT id,category,payload,recorded_at,occurred_at FROM memories WHERE scope=? AND subject='self' AND split='train' AND basis='direct' AND status='confirmed' ORDER BY recorded_at DESC,id DESC",(entry['scope'],)):
                sources=repo.sources('memory',row['id'])
                if sources and all(s.message_id in approved and s.conversation_id==entry['scope'] and s.direction=='outgoing' and s.speaker_id=='self' for s in sources):
                    payload=json.loads(row['payload'])
                    memories.append({'id':row['id'],'category':row['category'],'summary':payload.get('summary') or payload.get('value') or '',
                        'recorded_at':row['recorded_at'],'occurred_at':row['occurred_at'],'source_count':len(sources)})
        return {'documents':documents,'memories':memories,'facts':self._facts(session),'profile':profile,'skills':self.agent.skills.catalog(include_disabled=True)}

    def _import(self,payload):
        name=str(payload.get('name','')).strip();content=str(payload.get('content',''))
        if '/' in name or '\\' in name or Path(name).suffix.lower() not in ('.txt','.md') or len(name)>120:raise UIError('请输入 TXT/MD 文件名，不含目录。')
        if not content.strip() or len(content)>500000 or '\x00' in content:raise UIError('资料须为非空文本，最多 50 万字。')
        if not self.agent.retriever:raise UIError('未配置应用检索库，无法导入资料。')
        repo=self.agent.retrieval_repository
        doc=repo.upsert_document(name,content.lstrip('\ufeff'))
        self._progress('更新资料索引')
        try:self.agent.retriever.refresh('general');warning=None
        except Exception:warning='资料已保存；索引刷新失败，下次查询将重试。'
        return {'id':doc,'saved':True,'warning':warning}

    def _delete_document(self,document_id):
        repo=self.agent.retrieval_repository
        if repo is None:raise UIError('未配置资料库。')
        row=repo.conn.execute("SELECT id FROM knowledge_documents d WHERE id=? AND purpose='knowledge' AND NOT EXISTS(SELECT 1 FROM document_sources s WHERE s.document_id=d.id)",(int(document_id),)).fetchone()
        if not row:raise UIError('独立导入资料不存在。',404)
        repo.delete_document(row[0])
        try:self.agent.retriever.refresh('general');warning=None
        except Exception:warning='资料已删除；索引刷新失败，下次查询将重试。'
        return {'deleted':True,'warning':warning}

    def _refresh(self):
        if not self.agent.retriever:raise UIError('未配置检索库。')
        self._progress('更新资料索引');return {'counts':self.agent.retriever.refresh()}

    def _skill(self,payload):
        name=payload.get('name');enabled=payload.get('enabled')
        if type(enabled) is not bool:raise UIError('技能开关必须为布尔值。')
        if name not in self.agent.skills._skills:raise UIError('技能不存在。',404)
        self.agent.skills.enable(name,enabled)
        with self.meta:self.meta.execute('INSERT INTO ui_skills VALUES(?,?) ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled',(name,int(enabled)))
        return {'name':name,'enabled':enabled}

    def _mode(self,mode):
        if mode not in ('demo','live'):raise UIError('模式不正确。')
        if mode=='live' and not self.live_available():raise UIError('真实模型模式需要已配置的本机模型服务。')
        if mode==self.settings.mode:return {'mode':mode}
        enabled=set(self.agent.skills._enabled)
        candidate_settings=self.settings.model_copy(update={'mode':mode})
        candidate=ObservedAgent(candidate_settings,self._progress)
        for name in candidate.skills._skills:candidate.skills.enable(name,name in enabled)
        self.agent.close();self.settings=candidate_settings;self.agent=candidate
        with self.meta:self.meta.execute("INSERT INTO ui_settings VALUES('mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(mode,))
        return {'mode':mode}

    def close(self):
        def stop():self.agent.close();self.meta.close()
        self.pool.submit(stop).result();self.pool.shutdown()
