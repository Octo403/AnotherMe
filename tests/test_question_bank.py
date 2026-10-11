import json
import os
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.models import ResultBinding, SkillCall
from aitown.question_bank.jobs import create_job, expand_zip
from aitown.question_bank.models import BankConfig, COURSE_ID, GetArgs, Page, ParsedSource, Question, SearchArgs, Source
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank, citations
from aitown.question_bank.chatgpt import SimilarSelection, SelectedQuestion
from aitown.skills import SkillRegistry
from aitown.skills.question_bank import CourseStatus, QuestionGet, QuestionSearch


@pytest.fixture
def bank(tmp_path):
    config = BankConfig(root=tmp_path/'bank')
    repo = BankRepository(config.database)
    repo.save_discovery([{'id': COURSE_ID, 'name': '集合与图论', 'path': 'resources/course'}], [])
    sources = []
    for n, (year, exam, texts) in enumerate([
        (2022, 'final', ['证明等价关系的等价类构成划分。', '证明欧拉图的每个顶点度为偶数。']),
        (2025, 'midterm', ['证明等价关系具有传递性。']),
    ]):
        with repo.conn:
            sid = repo.save_source(Source(course_id=COURSE_ID, repository='synthetic/repo', path=f'exam{n}.txt', url='https://example.org/exam'+str(n)))
        repo.replace_parsed(sid, ParsedSource(pages=[Page(ordinal=1, text='\n'.join(texts), location_kind='line')],
            questions=[Question(number=str(i), text=t, pages=[1], topics=['等价关系' if '等价' in t else '欧拉图'], question_type='证明') for i,t in enumerate(texts,1)],
            category='exam', year=year, exam=exam))
        sources.append(sid)
    yield config, repo, QuestionBank(config), sources
    repo.close()


def test_exact_filters_and_no_result(bank):
    config, repo, service, _ = bank
    result = service.search(SearchArgs(query='等价关系证明题', year=2022, exam='final', question_type='证明'))
    assert result.count == 1 and result.results[0]['year'] == 2022
    assert service.search(SearchArgs(query='等价关系', year=1999)).status == 'no_result'
    assert service.search(SearchArgs(course='另一门课', query='等价关系')).status == 'needs_preparation'
    assert service.search(SearchArgs(query='量子纠缠')).status == 'no_result'
    assert 'message_refs' not in {r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_course_and_type_only_browse_without_character_match(bank):
    _, _, service, _ = bank
    result=service.search(SearchArgs(query='找集合与图论的证明题',question_type='证明'))
    assert result.count==3 and result.method=='filters'
    assert all(q['question_type']=='证明' and q['match_reason']=='符合筛选条件：证明题' for q in result.results)


def test_type_correction_keeps_course_filters_and_does_not_revive_after_personal_turn(bank,tmp_path):
    config, _, _, _ = bank
    with closing(AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))) as agent:
        first=agent.ask('找集合与图论2022年期末等价关系的证明题','correction')
        assert first.skills[0].data['count']==1
        follow=agent.ask('我说的是证明题','correction')
        args=follow.tool_calls[0].arguments
        assert follow.tool_calls[0].name=='question_search'
        assert args['year']==2022 and args['exam']=='final' and args['question_type']=='证明'
        assert '等价关系' in args['query']
        changed=agent.ask('不是计算题，我要证明题，换成欧拉图','correction')
        assert changed.tool_calls[0].arguments['question_type']=='证明'
        assert '等价关系' not in changed.tool_calls[0].arguments['query']
        assert changed.skills[0].data['count']==1
        agent.ask('我以前喜欢喝什么？','correction')
        unrelated=agent.ask('我说的是证明题','correction')
        assert not any(c.name.startswith('question_') for c in unrelated.tool_calls)


def test_api_first_search_followup_and_private_data_isolation(bank,tmp_path,monkeypatch):
    import httpx
    from aitown.providers import ProviderConfig
    config, repo, _, sources = bank
    qid=repo.rows(COURSE_ID,year=2022,exam='final',question_type='证明')[0]['id']
    with repo.conn:
        repo.conn.execute('INSERT INTO answers(question_id,source_id,text,locations,basis) VALUES(?,?,?,?,?)',
            (qid,sources[0],'PRIVATE_ANSWER_SENTINEL','[1]','verified'))
    requests=[]
    def handle(request):
        assert request.headers['Authorization']=='Bearer test-key'
        payload=json.loads(request.content);requests.append(payload)
        user=json.loads(payload['messages'][-1]['content'])
        if 'previous_search' in user:
            result={'query':'集合与图论 等价关系证明题','year':2022,'exam':'final','question_type':'证明','limit':1,'clarification':''}
        else:
            assert any(q['id']==qid for q in user['candidates'])
            result={'selected':[{'id':qid,'reason':'等价关系与证明题型符合要求'}],'missing':''}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]})
    original_client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=httpx.MockTransport(handle),**kwargs))
    settings=Settings(question_bank_dir=config.root,question_similar_provider='api',
        question_api=ProviderConfig(model='custom-exact-model',api_key='test-key'),
        memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge')
    with closing(AnotherMeAgent(settings)) as agent:
        agent.ask('我以前喜欢 PRIVATE_MEMORY_SENTINEL','api-course')
        first=agent.ask('找集合与图论2022年期末等价关系证明题','api-course')
        follow=agent.ask('我说的是证明题','api-course')
        assert first.skills[0].data['method'].endswith('+api_selection')
        assert follow.skills[0].data['question_ids']==[qid]
        agent.ask('我说的是我的经历 PRIVATE_MEMORY_SENTINEL','api-course')
    assert len(requests)==4
    serialized=json.dumps(requests,ensure_ascii=False)
    assert 'PRIVATE_MEMORY_SENTINEL' not in serialized and 'PRIVATE_ANSWER_SENTINEL' not in serialized
    assert all(p['model']=='custom-exact-model' for p in requests)
    assert json.loads(requests[2]['messages'][-1]['content'])['previous_search']['year']==2022


def test_empty_result_keeps_question_context_for_correction(bank,tmp_path):
    config, _, _, _ = bank
    with closing(AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))) as agent:
        first=agent.ask('找集合与图论1999年等价关系证明题','empty-course')
        assert first.skills[0].data['status']=='no_result'
        follow=agent.ask('不限年份，只要证明题','empty-course')
        assert follow.tool_calls[0].name=='question_search'
        assert follow.tool_calls[0].arguments.get('year') is None
        assert follow.skills[0].data['count']==2


def test_question_selection_still_works_after_course_clarification(bank,tmp_path):
    config, _, _, _ = bank
    with closing(AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))) as agent:
        first=agent.ask('找集合与图论的证明题','clarify-course')
        qid=first.skills[0].data['question_ids'][1]
        clarification=agent.ask('再找两道同类型题','clarify-course')
        assert clarification.decision.needs_clarification
        selected=agent.ask('第二题完整给我','clarify-course')
        assert selected.tool_calls[0].name=='question_get'
        assert selected.tool_calls[0].arguments['question_ids']==[qid]


def test_new_complete_course_request_resets_previous_topic_and_filters(bank,tmp_path):
    config, _, _, _ = bank
    with closing(AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))) as agent:
        agent.ask('找集合与图论2022年期末等价关系证明题','fresh-course')
        fresh=agent.ask('找集合与图论的证明题','fresh-course')
        assert fresh.skills[0].data['count']==3 and fresh.skills[0].data['method']=='filters'
        assert not fresh.tool_calls[0].arguments.get('year') and not fresh.tool_calls[0].arguments.get('exam')


def test_api_rejects_invented_candidate_and_labels_fallback(bank,monkeypatch):
    from aitown.providers import ProviderConfig
    config, _, service, _ = bank
    config.similar_provider='api';config.api=ProviderConfig(model='test',api_key='key')
    monkeypatch.setattr(ProviderConfig,'structured',lambda *args:SimilarSelection(
        selected=[SelectedQuestion(id=999999,reason='invented')],missing=''))
    result=service.search(SearchArgs(query='等价关系证明题',question_type='证明'))
    assert result.count==2 and result.model_calls[0]['status']=='failed'
    assert any('仅为本地筛选' in w for w in result.warnings)


def test_readonly_missing_bank_does_not_create_files(tmp_path):
    config = BankConfig(root=tmp_path/'missing')
    service = QuestionBank(config)
    assert service.search(SearchArgs(query='等价关系')).status == 'needs_preparation'
    assert service.get(GetArgs(question_ids=[12])).status == 'needs_preparation'
    assert not config.root.exists()


def test_typed_binding_answers_opt_in_and_disabled(bank):
    config, repo, service, sources = bank
    registry = SkillRegistry()
    for skill in (QuestionSearch(service), QuestionGet(service), CourseStatus(service)):
        registry.register(skill)
    calls = [SkillCall(name='question_search', arguments={'query':'等价关系'}),
        SkillCall(name='question_get', bindings=[ResultBinding(argument='question_ids', call_index=0, field='question_ids')])]
    registry.validate_plan(calls)
    result = registry.execute(calls[0])
    qid = result.data['question_ids'][0]
    with repo.conn:
        repo.conn.execute('INSERT INTO answers(question_id,source_id,text,locations,basis) VALUES(?,?,?,?,?)', (qid,sources[0],'synthetic secret answer','[1]','synthetic verified pair'))
    hidden = registry.execute(registry.resolve(calls[1],[result]))
    assert hidden.ok and 'synthetic secret answer' not in json.dumps(hidden.data)
    shown = registry.execute(SkillCall(name='question_get',arguments={'question_ids':[qid], 'include_answer':True}))
    assert shown.ok and shown.data['results'][0]['answers'][0]['text']=='synthetic secret answer'
    registry.enable('question_get',False)
    assert not registry.execute(SkillCall(name='question_get',arguments={'question_ids':[qid]})).ok
    assert not registry.execute(SkillCall(name='question_search',arguments={'query':'等价','database':'private.sqlite3'})).ok


def test_reprocessing_invalidates_ids_and_citations(bank):
    config, repo, service, sources = bank
    registry = SkillRegistry();registry.register(QuestionSearch(service))
    result = registry.execute(SkillCall(name='question_search',arguments={'query':'等价关系', 'year':2022}))
    citation = citations(result)[0]; old = result.data['question_ids'][0]
    assert service.current_citation(citation)
    repo.replace_parsed(sources[0],ParsedSource(pages=[],questions=[Question(number='1',text='证明新的等价关系条件。',pages=[1],topics=['等价关系'])],category='exam',year=2022,exam='final'))
    assert service.get(GetArgs(question_ids=[old])).status=='invalid_ids'
    assert not service.current_citation(citation)
    new = service.search(SearchArgs(query='等价关系',year=2022)).question_ids[0]
    assert new != old
    with repo.conn:repo.conn.execute('UPDATE sources SET remote_removed=1 WHERE id=?',(sources[0],))
    assert service.get(GetArgs(question_ids=[new])).status=='invalid_ids'


def test_zip_traversal_and_limits(bank,tmp_path):
    config, repo, _, sources = bank
    path = tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('../escape.txt','bad')
    with pytest.raises(ValueError,match='路径'):expand_zip(repo,repo.source(sources[0]),path,config)
    assert not (tmp_path/'escape.txt').exists()
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('nested/exam.txt','1. 证明等价关系。')
    ids = expand_zip(repo,repo.source(sources[0]),path,config)
    assert len(ids)==1 and repo.source(ids[0])['member']=='nested/exam.txt'
    assert len(expand_zip(repo,repo.source(sources[0]),path,config))==1
    small = config.model_copy(update={'max_archive_bytes':1})
    with pytest.raises(ValueError,match='限制'):expand_zip(repo,repo.source(sources[0]),path,small)


def test_real_worker_process_interruption_and_resume(bank,tmp_path):
    config, repo, service, _ = bank
    ids=[]
    for n in range(2):
        file=tmp_path/f'seed{n}.txt';file.write_text(f'{n+1}. 证明等价关系{n}。',encoding='utf-8')
        with repo.conn:sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path='fixture.zip',member=file.name,url='https://example.org/fixture'))
        repo.set_source(sid,local_path=str(file));ids.append(sid)
    with repo.conn:
        cursor=repo.conn.execute("INSERT INTO jobs(course_id,status,stage,source_ids,updated_at) VALUES(?,'queued','queued',?,0)",(COURSE_ID,json.dumps(ids)))
        job_id=cursor.lastrowid
    script = ('import os,json; from aitown.question_bank.models import BankConfig; from aitown.question_bank.jobs import run_job; '
        f'run_job(BankConfig(root={str(config.root)!r}),{job_id},progress=lambda s: os._exit(73) if json.loads(s)["done"]==1 else None)')
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    stopped=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,timeout=60)
    assert stopped.returncode==73,stopped.stderr
    first_id=repo.conn.execute('SELECT id FROM questions WHERE source_id=?',(ids[0],)).fetchone()[0]
    resumed=subprocess.run([sys.executable,'-m','aitown.question_bank','--root',str(config.root),'resume','--job-id',str(job_id)],env=env,capture_output=True,timeout=60)
    assert resumed.returncode==0,resumed.stderr
    assert repo.conn.execute('SELECT id FROM questions WHERE source_id=?',(ids[0],)).fetchone()[0]==first_id
    assert repo.conn.execute('SELECT count(*) FROM questions WHERE source_id IN (?,?)',ids).fetchone()[0]==2
    assert repo.conn.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()[0]=='complete'


def test_agent_question_followup_and_session_isolation(bank,tmp_path):
    config,repo,service,_=bank
    settings=Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge')
    agent=AnotherMeAgent(settings)
    try:
        reply=agent.ask('找集合与图论有关等价关系的证明题，先别给答案','course')
        assert reply.tool_calls[0].name=='question_search' and reply.sources
        assert all(s.subject=='general' and not s.source_ids and s.external_url for s in reply.sources)
        selected=reply.skills[0].data['question_ids'][1]
        second=agent.ask('把第二题完整给我','course')
        assert second.tool_calls[0].arguments['question_ids']==[selected]
        assert not second.tool_calls[0].arguments['include_answer']
        again=agent.ask('再找两道同类型题','course')
        assert again.tool_calls[0].name=='question_search' and selected not in again.skills[0].data['question_ids']
        other=agent.ask('把第二题完整给我','other')
        assert all(c.name!='question_get' for c in other.tool_calls)
        concept=agent.ask('解释集合与图论中的等价关系','concept')
        assert all(c.name!='question_search' for c in concept.tool_calls)
        assert agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]==0
    finally:agent.close()


def test_chatgpt_selects_query_and_reference_followup_without_recalling_on_get(bank,tmp_path):
    config,repo,service,_=bank
    class Selector:
        calls=[]
        def select(self,query,references,candidates,limit):
            self.calls.append(len(references))
            assert not references or (len(references)==1 and references[0]['id'] not in [q['id'] for q in candidates])
            matching=[q for q in candidates if '等价关系' in q['topics']]
            return SimilarSelection(selected=[SelectedQuestion(id=q['id'],reason='同属等价关系证明') for q in matching[:limit]],missing=''),{'provider':'codex_chatgpt','status':'ok'}
    selector=Selector()
    settings=Settings(question_bank_dir=config.root,question_similar_provider='codex_chatgpt',memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge')
    agent=AnotherMeAgent(settings);agent.question_bank.similarity=selector
    try:
        first=agent.ask('找集合与图论等价关系的证明题','course')
        assert selector.calls==[0] and 'chatgpt_selection' in first.skills[0].data['method']
        agent.ask('把第二题完整给我','course')
        assert selector.calls==[0]
        again=agent.ask('再找两道同类型题','course')
        assert selector.calls==[0,1] and 'chatgpt_similar' in again.skills[0].data['method']
        assert all(q['match_reason']=='同属等价关系证明' for q in again.skills[0].data['results'])
        assert len(again.sources)==1
    finally:agent.close()


def test_chatgpt_can_reject_local_matches_and_checks_candidates_with_exact_filters(bank):
    config,_,bank_service,_=bank
    config.similar_provider='codex_chatgpt'
    class Reject:
        def select(self,query,references,candidates,limit):
            assert not references and candidates
            assert all(q['year']==2022 and q['exam']=='final' and q['question_type']=='证明' for q in candidates)
            return SimilarSelection(selected=[],missing='未发现满足提问条件的候选题'),{'provider':'codex_chatgpt','status':'ok'}
    bank_service.similarity=Reject()
    for query in ('等价关系','非阿贝尔群的表示'):
        result=bank_service.search(SearchArgs(query=query,year=2022,exam='final',question_type='证明'))
        assert result.status=='no_result' and not result.results
        assert result.model_calls[0]['status']=='ok' and 'chatgpt_selection' in result.method


def test_explicit_ordinal_similar_search_uses_reference_instead_of_get(bank,tmp_path):
    config,_,_,_=bank
    with closing(AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))) as agent:
        first=agent.ask('找集合与图论等价关系的证明题','direct-reference')
        first_id=first.skills[0].data['results'][0]['id']
        similar=agent.ask('以第一题为参照，再找一道同类型题','direct-reference')
        assert similar.tool_calls[0].name=='question_search'
        assert similar.tool_calls[0].arguments['reference_ids']==[first_id]
        assert all(q['id']!=first_id for q in similar.skills[0].data['results'])


def test_chatgpt_failure_explicitly_returns_only_local_results(bank):
    config,_,bank_service,_=bank
    config.similar_provider='codex_chatgpt'
    class Failure:
        def select(self,*args):raise TimeoutError('synthetic-timeout')
    bank_service.similarity=Failure()
    result=bank_service.search(SearchArgs(query='等价关系'))
    assert result.results and result.model_calls[0]['status']=='failed'
    assert 'chatgpt_selection' not in result.method
    assert any('不冒充模型判断' in warning for warning in result.warnings)


def test_bge_filters_before_vectors_and_reranking(bank):
    config,repo,service,_=bank
    config.embedding_dir=Path('synthetic-embedding');config.reranker_dir=Path('synthetic-reranker')
    class Semantic:
        def scores(self,query,candidates):
            assert all(q['year']==2022 and q['exam']=='final' for q in candidates)
            return {q['id']:.8 for q in candidates}
        def rerank(self,query,questions):
            assert all(q['year']==2022 for q in questions)
            return [.9]*len(questions)
    service.semantic=Semantic()
    result=service.search(SearchArgs(query='等价关系',year=2022,exam='final'))
    assert result.method=='bge+keyword+bge_rerank' and result.count==1
    class Broken:
        def scores(self,*args):raise RuntimeError('offline')
    service.semantic=Broken()
    fallback=service.search(SearchArgs(query='等价关系',year=2022))
    assert fallback.method=='keyword_fallback' and fallback.warnings and fallback.count==1


def test_question_image_allowlist(bank,tmp_path):
    from types import SimpleNamespace
    from aitown.ui.service import UIService,UIError
    config,repo,bank_service,_=bank
    qid=repo.rows(COURSE_ID)[0]['id']
    allowed=config.root/'processed'/'test.png';allowed.parent.mkdir(parents=True);allowed.write_bytes(b'local image fixture')
    with repo.conn:repo.conn.execute('UPDATE questions SET images=? WHERE id=?',(json.dumps([str(allowed)]),qid))
    service=object.__new__(UIService);service.settings=SimpleNamespace(question_bank_dir=config.root);service.agent=SimpleNamespace(question_bank=bank_service)
    assert service._question_image(qid,0)==(b'local image fixture','image/png')
    with pytest.raises(UIError):service._question_image(qid,-1)
    outside=tmp_path/'outside.png';outside.write_bytes(b'outside')
    with repo.conn:repo.conn.execute('UPDATE questions SET images=? WHERE id=?',(json.dumps([str(outside)]),qid))
    with pytest.raises(UIError):service._question_image(qid,0)


def test_cross_page_previews_and_current_original_page_access(bank,tmp_path):
    from types import SimpleNamespace
    from aitown.question_bank.models import ImageRegion
    from aitown.ui.service import UIService,UIError
    config,repo,bank_service,sources=bank
    folder=config.root/'processed'/'cross-page';folder.mkdir(parents=True)
    originals=[folder/f'page-{n}.png' for n in (1,2,3)]
    crops=[folder/f'question-page-{n}.png' for n in (1,2)]
    for path in originals+crops:path.write_bytes(path.name.encode())
    repo.replace_parsed(sources[0],ParsedSource(
        pages=[Page(ordinal=n,text='原页',image=str(path),printed_label=str(n+4)) for n,path in enumerate(originals,1)],
        questions=[Question(number='一',text='共享题干和跨页小问',pages=[1,2],images=list(map(str,crops)),
            image_regions=[ImageRegion(index=i,page=i+1,bbox=[0,10,200,100],printed_label='4' if i==0 else None) for i in range(2)])]))
    qid=repo.rows(COURSE_ID)[0]['id'];q=repo.get([qid])[0]
    assert [(p['page'],p['printed_label'],p['kind']) for p in q['image_previews']]==[(1,'4','crop'),(2,'6','crop')]
    assert [p['ordinal'] for p in q['source_pages']]==[1,2]
    service=object.__new__(UIService);service.settings=SimpleNamespace(question_bank_dir=config.root);service.agent=SimpleNamespace(question_bank=bank_service)
    assert service._question_page(qid,2)==(b'page-2.png','image/png')
    with pytest.raises(UIError):service._question_page(qid,3)  # Same source, different question's page.
    outside=tmp_path/'outside.png';outside.write_bytes(b'outside')
    with repo.conn:repo.conn.execute('UPDATE pages SET image=? WHERE source_id=? AND ordinal=2',(str(outside),sources[0]))
    with pytest.raises(UIError):service._question_page(qid,2)
    repo.set_source(sources[0],status='failed')
    with pytest.raises(UIError):service._question_page(qid,1)


def test_image_similarity_manifest_is_bound_and_excludes_answers(bank,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from aitown.question_bank import chatgpt
    config,repo,_,_=bank
    folder=config.root/'processed';folder.mkdir(parents=True)
    paths=[folder/f'{i}.png' for i in range(3)]
    for p in paths:p.write_bytes(b'image')
    ref=repo.rows(COURSE_ID)[0];candidate=repo.rows(COURSE_ID)[1]
    ref.update(images=list(map(str,paths[:2])),image_previews=[{'index':i,'page':i+1,'kind':'crop'} for i in range(2)],answers=['ANSWER_MUST_NOT_BE_SENT'])
    candidate.update(images=[str(paths[2])],image_previews=[{'index':0,'page':7,'kind':'crop'}])
    monkeypatch.setattr(chatgpt,'codex_command',lambda:['fixture-codex'])
    def run(args,**kwargs):
        if args[-2:]==['login','status']:return SimpleNamespace(returncode=0,stdout='Logged in using ChatGPT',stderr='')
        prompt=kwargs['input'];assert 'ANSWER_MUST_NOT_BE_SENT' not in prompt and str(folder) not in prompt
        assert args.count('--image')==3 and args.index('-')<args.index('--image')
        Path(args[args.index('-o')+1]).write_text(json.dumps({'selected':[{'id':candidate['id'],'reason':'原图显示相同的条件'}],'missing':''}),encoding='utf-8')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(chatgpt.subprocess,'run',run)
    selector=chatgpt.ChatGPTSimilarity(image_root=folder)
    result,call=selector.select('相似题',[ref],[candidate],1)
    assert result.selected[0].id==candidate['id'] and call['input_mode']=='text+image'
    assert [(x['question_id'],x['page']) for x in call['image_bindings']]==[(ref['id'],1),(ref['id'],2),(candidate['id'],7)]
    candidate['images']=[str(tmp_path/'outside.png')]
    with pytest.raises(ValueError,match='目录'):selector.inputs('相似题',[ref],[candidate],1)
    # A long question must not be presented with only its first few pages.
    candidate['images']=list(map(str,paths))*6
    candidate['image_previews']=[{'index':i,'page':i+1,'kind':'crop'} for i in range(18)]
    data,attached=selector.inputs('相似题',[ref],[candidate],1)
    assert len(attached)==2 and data['candidates'][0]['image_numbers']==[]
    assert data['candidates'][0]['original_image_count']==18 and data['candidates'][0]['image_note']


def test_reference_itself_is_never_a_similar_candidate(bank):
    config,repo,service,_=bank
    qid=service.search(SearchArgs(query='等价关系')).question_ids[0]
    result=service.search(SearchArgs(query='等价关系',reference_ids=[qid]))
    assert qid not in result.question_ids


def test_mixed_answers_are_not_part_of_question_or_crop(tmp_path):
    from aitown.question_bank.prepare import parse_pdf
    import pymupdf as fitz
    # English numeric headings avoid depending on an installed Chinese PDF font.
    path=tmp_path/'mixed.pdf'
    doc=fitz.open();page=doc.new_page()
    page.insert_text((30,40),'1. Prove the theorem.')
    page.insert_text((30,90),'2. Calculate the number.')
    doc.save(path);doc.close()
    # Exercise the splitter's Chinese answer markers with measured PDF coordinates.
    from aitown.question_bank.prepare import split_lines
    with fitz.open(path) as doc:
        result=split_lines([],[(1,30,45,'1. 证明条件。'),(1,55,65,'解：秘密答案'),
            (1,80,95,'2. 计算结果。'),(1,110,125,'参考答案'),(1,130,140,'秘密解析')],
            pdf=doc,output=tmp_path)
    assert len(result)==2 and all('秘密' not in q.text for q in result)
    with fitz.open(result[0].images[0]) as image:
        assert image[0].rect.height < 50


def test_live_question_display_never_asks_chat_model_to_solve(bank,tmp_path):
    config,repo,service,_=bank
    agent=AnotherMeAgent(Settings(mode='live',api_key='synthetic',model='synthetic',
        question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',knowledge_dir=tmp_path/'knowledge'))
    class NoFreeAnswer:
        def complete(self,*args,**kwargs):
            raise AssertionError('找题不应调用自由回答模型')
        def close(self):
            pass
    agent.model.close()
    agent.model=NoFreeAnswer()
    try:
        reply=agent.ask('找集合与图论等价关系的证明题，先别给答案','course')
        assert reply.stop_reason=='answered' and reply.sources and not reply.model_calls
        assert not reply.errors
    finally:agent.close()


def test_ui_hides_extracted_question_text_in_chat_history_and_tool_details(bank,tmp_path):
    from aitown.ui.service import UIService
    config,repo,_,_=bank
    qid=repo.rows(COURSE_ID)[0]['id']
    raw='证明等价关系的等价类构成划分。\n□ k S a a\n装\n订\n线\nOCR数学符号破损'
    image=config.root/'processed'/'original.png'
    image.parent.mkdir(parents=True);image.write_bytes(b'original question fixture')
    with repo.conn:
        repo.conn.execute('UPDATE sources SET path=? WHERE id=?',('exam0.pdf',repo.rows(COURSE_ID)[0]['source_id']))
        repo.conn.execute('UPDATE questions SET text=?,images=? WHERE id=?',(raw,json.dumps([str(image)]),qid))
    service=UIService(Settings(question_bank_dir=config.root,memory_db=tmp_path/'history.sqlite3',
        knowledge_dir=tmp_path/'knowledge'),tmp_path/'ui.sqlite3')
    try:
        sid=service.call('_create_session',{'scope':''})['id']
        shown=service.call('_chat',{'session':sid,'question':'找集合与图论等价关系的证明题'})['reply']
        selected=next(q for q in shown['questions'] if q['id']==qid)
        assert 'text' not in selected and selected['images']==[str(image)] and selected['url']
        assert all('text' not in q for skill in shown['skills'] for q in skill['data'].get('results',[]) if q['content_format']=='image')
        assert 'OCR数学符号破损' not in json.dumps(shown,ensure_ascii=False)
        assert service.call('_question_image',qid,0)==(image.read_bytes(),'image/png')
        history=service.call('_history',sid)
        assert 'OCR数学符号破损' not in json.dumps(history,ensure_ascii=False)
        original=service.pool.submit(lambda:service.agent.question_bank.get(GetArgs(question_ids=[qid])).results[0]).result()
        assert original['text']==raw
        stored=service.pool.submit(lambda:service.agent.memory.conn.execute(
            'SELECT reply_json FROM runtime_runs WHERE id=?',(shown['request_id'],)).fetchone()[0]).result()
        stored_questions=json.loads(stored)['skills'][0]['data']['results']
        assert next(q for q in stored_questions if q['id']==qid)['text']==raw
        replacement=image.with_name('completed-word-crop.png');replacement.write_bytes(b'new crop')
        with repo.conn:repo.conn.execute('UPDATE questions SET images=? WHERE id=?',(json.dumps([str(replacement)]),qid))
        refreshed=service.call('_history',sid)
        assert replacement.name in json.dumps(refreshed,ensure_ascii=False)
        # Re-render a previously saved reply; no OCR text was deleted or re-ingested.
        def reopen():
            service.agent.close();service.meta.close();service._start()
        service.pool.submit(reopen).result()
        assert 'OCR数学符号破损' not in json.dumps(service.call('_history',sid),ensure_ascii=False)
    finally:service.close()


def test_explicit_update_discovers_new_and_removed_files(bank,tmp_path,monkeypatch):
    from aitown.question_bank import jobs
    config,repo,service,sources=bank
    current=Source(course_id=COURSE_ID,repository='synthetic/repo',path='exam0.txt',url='https://example.org/current')
    new=Source(course_id=COURSE_ID,repository='synthetic/repo',path='new.txt',url='https://example.org/new')
    updates=[]
    class Remote:
        def __init__(self,*args):pass
        def discover(self):return [{'id':COURSE_ID,'name':'集合与图论','path':'resources/course'}],[current,new]
        def download(self,source,path,*,update=False):
            updates.append(update);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('1. 证明等价关系的新条件。',encoding='utf-8');return path
        def close(self):pass
    monkeypatch.setattr(jobs,'GithubSource',Remote)
    job=create_job(repo,COURSE_ID,update=True)
    result=jobs.run_job(config,job,progress=lambda s:None)
    assert result['failed']==0 and updates==[True,True]
    assert repo.source(sources[1])['remote_removed']==1
    assert any(s['path']=='new.txt' for s in repo.sources(COURSE_ID))
    found=service.search(SearchArgs(query='等价关系')).results
    assert len(found)==1 and len(found[0]['origins'])==2


def test_failed_directory_refresh_never_removes_existing_sources(bank,monkeypatch):
    from aitown.question_bank import jobs
    config,repo,service,sources=bank
    class Offline:
        def __init__(self,*args):pass
        def discover(self):raise ConnectionError('synthetic network failure')
        def close(self):pass
    monkeypatch.setattr(jobs,'GithubSource',Offline)
    job=create_job(repo,COURSE_ID,update=True)
    with pytest.raises(ConnectionError):jobs.run_job(config,job,progress=lambda s:None)
    assert all(not repo.source(s)['remote_removed'] for s in sources)
    assert service.search(SearchArgs(query='等价关系')).count==2
    assert repo.conn.execute('SELECT discovery_error FROM courses WHERE id=?',(COURSE_ID,)).fetchone()[0]


def test_recollection_answer_document_is_never_indexed_as_questions(tmp_path):
    from aitown.question_bank.prepare import parse_file
    from aitown.question_bank.sources import hints
    path=tmp_path/'回忆参考答案.md'
    path.write_text('回忆整理的参考答案\n1. 秘密解题过程。',encoding='utf-8')
    source={'category_hint':hints(path.name)['category_hint']}
    parsed=parse_file(path,source,tmp_path/'processed')
    assert parsed.category=='answer' and parsed.questions==[] and parsed.status=='partial'


def test_reviewed_answer_pair_uses_source_locations_and_is_atomic(bank):
    from aitown.question_bank.models import AnswerPair
    config,repo,service,sources=bank
    with repo.conn:
        sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path='answer.txt',url='https://example.org/answer'))
    parsed=ParsedSource(pages=[Page(ordinal=20,text='source answer hint',location_kind='paragraph')],questions=[],category='answer',year=2022,exam='final')
    repo.replace_parsed(sid,parsed)
    q=repo.rows(COURSE_ID,year=2022)[0]
    pair=AnswerPair(question_number=q['number'],question_text=q['text'],locations=[20],basis='same paper and verified stem',missing=['Only a hint'])
    result=repo.pair_answers(sources[0],sid,[pair])
    assert result['paired']==1 and result['partial_answers']==1
    assert 'source answer hint' not in service.search(SearchArgs(query='等价关系')).model_dump_json()
    assert 'answers' not in service.get(GetArgs(question_ids=[q['id']])).results[0]
    shown=service.get(GetArgs(question_ids=[q['id']],include_answer=True)).results[0]['answers'][0]
    assert shown['text']=='source answer hint' and shown['location_kind']=='paragraph' and shown['status']=='partial'
    bad=pair.model_copy(update={'question_number':'2','question_text':'wrong stem'})
    with pytest.raises(ValueError,match='题干'):
        repo.pair_answers(sources[0],sid,[pair,bad])
    assert repo.conn.execute('SELECT count(*) FROM answers WHERE source_id=?',(sid,)).fetchone()[0]==1
    with pytest.raises(ValueError,match='位置'):
        repo.pair_answers(sources[0],sid,[pair.model_copy(update={'locations':[999]})])
    repo.replace_parsed(sid,parsed.model_copy(update={'pages':[Page(ordinal=20,text='changed answer')]}))
    assert not service.get(GetArgs(question_ids=[q['id']],include_answer=True)).results[0]['answer_available']


def test_answer_pair_cli_rejects_wrong_course_and_reads_current_answer(bank,tmp_path,capsys):
    from aitown.question_bank.__main__ import main
    config,repo,service,sources=bank
    with repo.conn:
        sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path='answer.txt',url='https://example.org/answer'))
    repo.replace_parsed(sid,ParsedSource(pages=[Page(ordinal=1,text='exact source text')],questions=[],category='answer'))
    q=repo.rows(COURSE_ID)[0]
    file=tmp_path/'pairs.json'
    file.write_text(json.dumps([{'question_number':q['number'],'question_text':q['text'],'locations':[1],'basis':'verified source'}]),encoding='utf-8')
    args=['--root',str(config.root),'pair-answers','--question-source-id',str(sources[0]),'--answer-source-id',str(sid),'--file',str(file)]
    assert main(args)==0
    assert main([*args,'--course','other-course'])==1
    assert service.get(GetArgs(question_ids=[q['id']],include_answer=True)).results[0]['answers'][0]['text']=='exact source text'


def test_two_column_crops_preserve_physical_pages_and_region_boundaries(tmp_path):
    from aitown.question_bank.prepare import split_lines
    import pymupdf as fitz
    doc=fitz.open();doc.new_page(width=800,height=600)
    rows=[(1,30,45,'1. Prove first.',0,400),(1,60,75,'continued.',0,400),
          (1,30,45,'continued on right.',400,800),(1,100,115,'2. Next.',400,800)]
    questions=split_lines([],rows,pdf=doc,output=tmp_path)
    assert len(questions)==2 and questions[0].pages==[1] and len(questions[0].images)==2
    with fitz.open(questions[0].images[1]) as image:
        assert image[0].rect.width < 700 and image[0].rect.height < 130
    doc.close()


def test_ocr_keeps_unreviewed_math_out_of_search_and_preserves_failure(tmp_path,monkeypatch):
    import pymupdf as fitz
    from aitown.question_bank import ocr
    from aitown.question_bank.prepare import parse_pdf
    doc=fitz.open();doc.new_page();path=tmp_path/'scan.pdf';doc.save(path);doc.close()
    config=BankConfig(root=tmp_path/'bank',pdf_ocr='windows')
    monkeypatch.setattr(ocr,'windows_ocr',lambda *args,**kwargs:[(1,10,20,'1. Wrong mathematical condition.',0,595)])
    parsed=parse_pdf(path,{'category_hint':'exam'},tmp_path/'recognized',config)
    assert parsed.status=='partial' and not parsed.questions and 'Wrong' in parsed.pages[0].text
    def failure(*args,**kwargs):raise ValueError('Missing local language')
    monkeypatch.setattr(ocr,'windows_ocr',failure)
    failed=parse_pdf(path,{'category_hint':'exam'},tmp_path/'failure',config)
    assert failed.status=='partial' and failed.pages[0].image and any('未完成' in note for note in failed.notes)
