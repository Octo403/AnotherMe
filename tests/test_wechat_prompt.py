import json

from aitown.models import Citation,Decision,SkillResult
from aitown.nodes.rag import filters_for
from aitown.nodes.reflect import evidence


def test_interest_query_does_not_use_an_event_as_preference():
    for question in ['我的编程兴趣如何？','我对代码一直很感兴趣吗？']:
        assert filters_for({'input':question,'decision':Decision(intent='knowledge',query=question,purpose='personal',subject='self'),
                            'style_scope':'scope'}).category=='preference'
    assert filters_for({'input':'我参加过什么兴趣小组？','decision':Decision(intent='knowledge',query='兴趣小组',purpose='personal',subject='self'),
                        'style_scope':'scope'}).category is None


def test_summary_prompt_keeps_complete_sources_and_counts_without_duplicate_transcript():
    sources=[Citation(source='message:'+str(i),chunk=0,score=1,text='没有去过月球；准备整理资料。'+str(i),subject='self',
                      timestamp=1750948698,source_ids=[i]) for i in range(1,14)]
    transcript='\n'.join(s.text for s in sources)
    result=SkillResult(name='dialogue_summary',ok=True,data={'content':transcript,'count':13,'episode_ids':[41],
                        'missing_parts':['一条压缩正文不可读'],'truncated':False,'evidence':[s.model_dump() for s in sources]})
    data=evidence({'sources':sources,'skills':[result]})
    assert data['tools'][0]['data']['count']==13
    assert data['tools'][0]['data']['missing_parts']==['一条压缩正文不可读']
    assert [s['text'] for s in data['sources']]==[s.text for s in sources]
    assert [s['ref'] for s in data['sources']]==[s.ref for s in sources]
    assert all(s['timestamp']==1750948698 and s['subject']=='self' for s in data['sources'])
    assert 'content' not in data['tools'][0]['data'] and result.data['content']==transcript
    assert len(json.dumps(data,ensure_ascii=False))<len(json.dumps({'sources':[s.model_dump() for s in sources],'tools':[result.model_dump()]},ensure_ascii=False))
