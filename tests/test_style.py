import json
from pathlib import Path

import pytest

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.models import AnswerPayload, Decision, ReviewPayload
from aitown.storage import Repository
from aitown.style import ProfileStore, StyleRetriever, compose_prompt, statistics
from aitown.style.evaluation import generate, prepare, summarize
from aitown.style.models import ProfileClaim, StyleProfile
from aitown.style.feedback import load_feedback, response_task, format_feedback_answer


@pytest.fixture
def corpus(tmp_path):
    repo=Repository(tmp_path/'app.sqlite3')
    pairs=[('明天一起看作业吗','嗯嗯\n好滴','train','c0001'),
           ('这个文件怎么保存','先另存文件，因为方便查看','train','c0001'),
           ('明天一起看作业吗','嗯嗯 私有地点墨谷古堡','test','c0001'),
           ('明天一起看作业吗','嗯嗯 茶谷旅途秘密','train','c0002')]
    with repo.conn:
        repo.conn.execute("INSERT INTO import_batches VALUES('synthetic','test',0,NULL,NULL,'ready')")
        for i,(question,answer,split,scope) in enumerate(pairs,1):
            mids=[i*2-1,i*2]
            repo.conn.execute("INSERT INTO episodes VALUES(?, 'synthetic', ?, ?, ?, ?, '[]')",(i,f'e{i}',scope,i*10,i*10+1))
            for mid,text,direction,speaker in [(mids[0],question,'incoming','other'),(mids[1],answer,'outgoing','self')]:
                repo.conn.execute("INSERT INTO message_refs VALUES(?, 'synthetic', ?, ?, ?, ?, ?, ?, 1, 'text', ?, 'approved')",(mid,mid,scope,scope,i*10+(direction=='outgoing'),direction,speaker,text))
                repo.conn.execute('INSERT INTO episode_messages VALUES(?,?,?)',(i,mid,direction=='outgoing'))
            context=json.dumps([{'role':'user','content':question,'source_ids':[mids[0]]}],ensure_ascii=False)
            repo.conn.execute("INSERT INTO style_examples VALUES(?, 'synthetic', ?, ?, 'synthetic', ?, ?, ?, '[]')",(i,f's{i}',i,split,context,answer))
            repo.conn.executemany('INSERT INTO style_sources VALUES(?,?,?,0)',[(i,mids[0],'context'),(i,mids[1],'target')])
    yield repo
    repo.close()


def test_feedback_scope_sources_and_demonstration_invalidation(corpus,tmp_path):
    ratings=tmp_path/'ratings.json'
    ratings.write_text(json.dumps({'rater':'synthetic_test','cases':[{'answers':[{'id':'F1','comment':'Synthetic user annotation'}]}]}),encoding='utf-8')
    path=tmp_path/'feedback.json'
    config={'scope':'c0001','ratings_path':str(ratings),'rules':[{'key':'short','text':'Keep short','scenes':['agree'],'rating_ids':['F1']}],
            'demonstrations':[{'style_id':1,'scene':'agree','input':'合成邀请','reply':'嗯嗯'},
                              {'style_id':3,'scene':'agree','input':'不应入提示','reply':'嗯嗯'},
                              {'style_id':4,'scene':'agree','input':'其他范围','reply':'嗯嗯'}]}
    path.write_text(json.dumps(config),encoding='utf-8')
    active=load_feedback(path,corpus,scope='c0001',question='一起吗',scene='agree')
    assert len(active['rules'])==1 and [d['style_id'] for d in active['demonstrations']]==[1]
    assert load_feedback(path,corpus,scope='',question='一起吗')=={}
    assert load_feedback(path,corpus,scope='c0002',question='一起吗')=={}
    config['rules'][0]['enabled']=False
    path.write_text(json.dumps(config),encoding='utf-8')
    assert not load_feedback(path,corpus,scope='c0001',question='一起吗',scene='agree')['rules']
    assert corpus.delete_source(2)
    assert not load_feedback(path,corpus,scope='c0001',question='一起吗',scene='agree')['demonstrations']


def test_feedback_rules_keep_separate_owner_round_sources(corpus,tmp_path):
    old=tmp_path/'old.json';new=tmp_path/'new.json';config=tmp_path/'config.json'
    old.write_text(json.dumps({'rater':'synthetic','cases':[{'answers':[{'id':'OLD','comment':'original comment'}]}]}),encoding='utf-8')
    new.write_text(json.dumps({'rater':'synthetic','cases':[{'answers':[{'id':'NEW','comment':'new comment'}]}]}),encoding='utf-8')
    config.write_text(json.dumps({'scope':'c0001','ratings_path':str(old),'rules':[
        {'key':'old','text':'old rule','scenes':['refuse'],'rating_ids':['OLD']},
        {'key':'new','text':'new rule','scenes':['refuse'],'rating_ids':['NEW'],'ratings_path':str(new)}]}),encoding='utf-8')
    loaded=load_feedback(config,corpus,scope='c0001',question='拒绝',scene='refuse')
    assert [rule['key'] for rule in loaded['rules']]==['old','new']
    assert str(new) not in json.dumps(loaded)
    new.write_text(json.dumps({'rater':None,'cases':[{'answers':[{'id':'NEW','comment':'new comment'}]}]}),encoding='utf-8')
    with pytest.raises(ValueError):load_feedback(config,corpus,scope='c0001',question='拒绝',scene='refuse')


def test_response_task_distinguishes_drafting_from_direct_reply():
    draft=response_task('帮我拒绝这次聚会，不要编理由','refuse')
    direct=response_task('我不想参加了','refuse')
    assert draft['mode']=='draft_for_user' and direct['mode']=='respond_to_user'
    assert '收信人' in draft['instruction'] and '不参加' in direct['refusal']
    assert draft['purpose']=='draft_refusal' and direct['purpose']=='acknowledge_refusal'
    other=response_task('对方说“我不参加了”，我该怎么回复他','refuse')
    assert other['mode']=='draft_for_user' and other['purpose']=='acknowledge_refusal'
    invitation=response_task('同学说周末聚餐，我不想去，帮我拒绝','refuse')
    assert invitation['purpose']=='draft_refusal'
    activity=response_task('同学说“这次我不报活动了”，帮我回复一句','refuse')
    assert activity['purpose']=='acknowledge_refusal'
    feedback={'scene':'agree','rules':[{'omit_terminal_full_stop':True}]}
    assert format_feedback_answer('好呀。',feedback)=='好呀'
    assert format_feedback_answer('还没想好...',feedback)=='还没想好...'
    assert format_feedback_answer('好的。',{})=='好的。'
    feedback['scene']='explain'
    assert format_feedback_answer('先保存。再导出。',feedback)=='先保存。再导出。'


def test_owner_refusal_rule_does_not_change_who_is_refusing(corpus,tmp_path):
    ratings=tmp_path/'ratings.json'
    ratings.write_text(json.dumps({'rater':'synthetic','cases':[{'answers':[{'id':'NEW','comment':'Use a complete refusal'}]}]}),encoding='utf-8')
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'scope':'c0001','ratings_path':str(ratings),'rules':[
        {'key':'refuse_complete','text':'Complete own refusal','scenes':['refuse'],'purposes':['draft_refusal'],'rating_ids':['NEW']}]}),encoding='utf-8')
    own=load_feedback(config,corpus,scope='c0001',question='帮我拒绝周五的聚会',scene='refuse')
    assert [r['key'] for r in own['rules']]==['refuse_complete']
    peer=load_feedback(config,corpus,scope='c0001',question='同学说“这次我不报活动了”，帮我回复一句',scene='refuse')
    assert peer['rules']==[]


def test_training_stats_preserve_short_words_and_scope(corpus):
    report=statistics(corpus)
    assert report['replies']==3
    g=next(g for g in report['groups'] if g['scope']=='c0001')
    assert g['replies']==2 and g['multiline_replies']==1
    assert g['consecutive_message_replies']==0  # One source containing newline is not two messages.
    assert g['tokens']['嗯']==2 and g['tokens']['嗯嗯']==1
    assert g['source_ids']==[2,4]
    assert all(g['relationship']=='unknown' for g in report['groups'])


def test_retrieval_filters_scope_split_and_ranks_only_input(corpus):
    retriever=StyleRetriever(corpus)
    hits=retriever.search('明天一起看作业吗',scope='c0001',scene='agree')
    assert [h.id for h in hits]==[1]
    assert retriever.search('明天一起看作业吗',scope='',scene='agree')==[]
    assert retriever.search('墨谷古堡',scope='c0001',scene='agree')==[]
    before=hits[0].score
    with corpus.conn:corpus.conn.execute("UPDATE style_examples SET target='嗯嗯 未出现在输入的秘密经历' WHERE id=1")
    after=retriever.search('明天一起看作业吗',scope='c0001',scene='agree')[0]
    assert after.score==before and '秘密经历' not in after.guidance


def test_profile_edit_disable_and_deleted_source(corpus,tmp_path):
    store=ProfileStore(corpus)
    profile=store.apply(store.generate())
    path=tmp_path/'profile.json'
    store.write(profile,path)
    loaded=store.read(path)
    assert store.active(loaded,'c0001')
    chosen=next(c for c in loaded.claims if c.scope=='c0001')
    chosen.enabled=False
    chosen.text='按任务需要简短回应，不机械模仿。'
    edited=store.apply(loaded)
    assert chosen.key not in [c.key for c in store.active(edited,'c0001')]
    assert corpus.delete_source(2)
    assert store.active(profile,'c0001')==[]
    with pytest.raises(ValueError):store.apply(profile)


def test_profile_rejects_other_speaker_holdout_and_inferred_certainty(corpus):
    store=ProfileStore(corpus)
    for ids,basis in (([1],'observed'),([6],'observed'),([2],'inferred')):
        claim=ProfileClaim(key='bad',text='unsupported',scope='c0001',basis=basis,source_ids=ids)
        with pytest.raises(ValueError):store.apply(StyleProfile(claims=[claim]))


def test_prompt_separates_facts_and_never_transplants_sample_experience(corpus):
    with corpus.conn:corpus.conn.execute("UPDATE style_examples SET target='嗯嗯 我去了墨谷古堡' WHERE id=1")
    hit=StyleRetriever(corpus).search('明天一起看作业吗',scope='c0001',scene='agree')[0]
    profile=ProfileStore(corpus).apply(ProfileStore(corpus).generate())
    prompt=compose_prompt('AnotherMe',facts=[{'text':'虚构事实证据:比赛在周五'}],claims=profile.claims,examples=[hit])
    assert '比赛在周五' in prompt
    assert '墨谷古堡' not in prompt and '明天一起看作业吗' not in prompt
    assert '事实证据' in prompt and '仅形式' in prompt and '不能移植' in prompt


def test_graph_opt_in_scope_and_reset_with_captured_model(corpus,tmp_path):
    store=ProfileStore(corpus)
    path=tmp_path/'profile.json'
    store.write(store.apply(store.generate()),path)
    ratings=tmp_path/'ratings.json'
    ratings.write_text(json.dumps({'rater':'synthetic_test','cases':[{'answers':[{'id':'F1','comment':'Synthetic feedback'}]}]}),encoding='utf-8')
    feedback=tmp_path/'feedback.json'
    feedback.write_text(json.dumps({'scope':'c0001','ratings_path':str(ratings),'rules':[{'key':'rule','text':'Keep the short draft','scenes':['agree'],'rating_ids':['F1']}]}),encoding='utf-8')
    settings=Settings(style_database=corpus.path,style_profile=path,style_feedback=feedback,knowledge_dir=tmp_path/'empty',memory_db=tmp_path/'history.sqlite3')
    agent=AnotherMeAgent(settings)
    captured=[]
    reviews=[]
    class Fake:
        def structured(self,messages,schema):
            if schema is Decision:return Decision(intent='chat',query='明天一起看作业吗')
            if schema is AnswerPayload:
                captured.append(messages[0]['content'])
                return AnswerPayload(answer='好呀')
            reviews.append(messages[0]['content'])
            return ReviewPayload(answer='好呀')
        def close(self):pass
    agent.model=Fake()
    try:
        reply=agent.ask('明天一起看作业吗',scope='c0001',scene='agree')
        assert reply.style_examples and reply.profile_evidence
        assert '好滴' in captured[-1]
        assert 'Keep the short draft' in captured[-1] and 'Keep the short draft' in reviews[-1]
        empty=agent.ask('你好',scope='')
        assert empty.style_examples==[] and empty.profile_evidence==[]
        assert 'Keep the short draft' not in captured[-1] and 'Keep the short draft' not in reviews[-1]
    finally:agent.close()


def test_evaluation_blind_mapping_pending_and_actual_rating_validation(corpus,tmp_path):
    profile=ProfileStore(corpus).apply(ProfileStore(corpus).generate())
    out=tmp_path/'evaluation'
    report=prepare(corpus,profile,out,'c0001')
    assert report['cases']==30 and report['trials']==90
    assert summarize(out)['completed_ratings']==0
    assert not summarize(out)['complete']
    ratings=json.loads((out/'ratings.json').read_text(encoding='utf-8'))
    assert 'variant' not in str(ratings) and all(a['answer'] is None for c in ratings['cases'] for a in c['answers'])
    class Fake:
        def structured(self,messages,schema):return AnswerPayload(answer='合成测试输出，不是实际评测结果')
    assert generate(Fake(),out)==90
    ratings=json.loads((out/'ratings.json').read_text(encoding='utf-8'))
    ratings['rater']='synthetic_test_rater'
    a=ratings['cases'][0]['answers'][0]
    a.update(naturalness=4,similarity=3,scene_fit=4,comment='synthetic validation only')
    (out/'ratings.json').write_text(json.dumps(ratings),encoding='utf-8')
    assert summarize(out)['completed_ratings']==1
    b=ratings['cases'][0]['answers'][1]
    b['comment']='Comment without numeric ratings must remain visible'
    (out/'ratings.json').write_text(json.dumps(ratings),encoding='utf-8')
    partial=summarize(out)
    assert partial['completed_ratings']==1 and partial['rated_cases']==1
    assert partial['paired_cases']==0 and len(partial['comments'])==2
    assert partial['comments'][1]['scored'] is False
    assert partial['paired_variants']['plain']['mean'] is None
    for row in ratings['cases'][0]['answers']:
        row.update(naturalness=4,similarity=3,scene_fit=4,comment='Synthetic paired aggregation check')
    (out/'ratings.json').write_text(json.dumps(ratings),encoding='utf-8')
    paired=summarize(out)
    assert paired['completed_ratings']==3 and paired['paired_cases']==1
    assert all(v['mean']['similarity']==3 for v in paired['paired_variants'].values())
    a['naturalness']=6
    (out/'ratings.json').write_text(json.dumps(ratings),encoding='utf-8')
    with pytest.raises(ValueError):summarize(out)
