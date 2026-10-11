import json

import pytest

from aitown.data_pipeline.episodes import Parameters, examples, merge, partition, split_episodes
from aitown.data_pipeline.normalize import normalize
from aitown.data_pipeline.pipeline import references_from_source


def msg(id, time, direction="incoming", text="明天去吗", **changes):
    return {"id":id,"timestamp":time,"direction":direction,"content":text,
            "conversation_id":"c0001","conversation_kind":"private","type":1,
            "status":"text","eligible_text":True,"speaker_id":"self" if direction=="outgoing" else "other",
            **changes}


def build(rows, params=None, refs=None, reviews=None):
    params, refs = params or Parameters(), refs or {}
    normalized, report = normalize(rows,"batch")
    turns = merge(normalized,params,refs)
    episodes = split_episodes(turns,params,refs)
    reviews = reviews if reviews is not None else {str(r['id']):"approved" for r in rows}
    accepted, proposed, excluded, links = examples(episodes,turns,refs,reviews,{},params)
    return normalized,turns,episodes,accepted,proposed,excluded,links


def test_stable_order_duplicate_import_and_invalid_time():
    a, b = msg(2,1700000000), msg(1,1700000000)
    normalized, report = normalize([a,b,dict(a),msg(3,None)],"batch")
    assert [r['id'] for r in normalized] == [1,2]
    assert report['duplicates']==1
    assert report['quarantined'][0]['message_id']==3
    with pytest.raises(ValueError,match="Conflicting"):
        normalize([a,{**a,"content":"different"}],"batch")
    # Different source keys with identical text remain distinct.
    assert len(normalize([a,msg(3,1700000000)],"batch")[0])==2


def test_merge_reply_lines_and_boundaries():
    rows=[msg(1,1700000000,"outgoing","去"),msg(2,1700000010,"outgoing","不过得晚点"),
          msg(3,1700000020,"outgoing","实验没跑完"),msg(4,1700000030,"incoming","好"),
          msg(5,1700000040,"outgoing","谢谢"),msg(6,1700000050,"outgoing","【语音内容未纳入】",type=34,status="media_omitted",eligible_text=False),
          msg(7,1700000060,"outgoing","继续"),msg(8,1700000200,"outgoing","再说")]
    turns=build(rows)[1]
    assert turns[0]['text']=="去\n不过得晚点\n实验没跑完"
    assert turns[0]['message_ids']==[1,2,3]
    assert len(turns)==6


def test_date_gap_missing_and_topic_boundaries():
    t=1700000000
    data=build([msg(1,t,text="讨论课题"),msg(2,t+10,"outgoing","对了，明天吃什么"),
                msg(3,t+20,status="compressed_unresolved",eligible_text=False),
                msg(4,t+30,text="另一个问题"),msg(5,t+2000,"outgoing","晚点回答"),
                msg(6,t+86400,text="隔天")])
    reasons=[r for e in data[2] for r in e['boundary_reasons']]
    assert {'topic_change','unavailable_or_non_plain','time_gap','date_change'} <= set(reasons)
    assert any(e['missing_message_ids']==[3] for e in data[2])


def test_async_short_reply_is_not_paired_by_adjacency():
    data=build([msg(1,1700000000,text="明天去吗"),msg(2,1700000500,"outgoing","可以")])
    assert data[3]==[]
    assert data[5]['ambiguous_asynchronous_reply']==1
    assert data[6][0]['status']=='ambiguous'


def test_explicit_long_reply_and_reference_connected_splits():
    refs={'2':{'status':'resolved','target_id':1}}
    data=build([msg(1,1700000000,text="明天去吗"),msg(2,1700086400,"outgoing","可以")],refs=refs)
    assert len(data[2])==1
    assert data[3][0]['quality_labels']==['explicit_reference']
    same_second = build([msg(1,1700000000),msg(2,1700000000,'outgoing','可以')],refs=refs)
    assert same_second[3][0]['context_message_ids']==[1]
    episodes=[{'episode_id':f'e{i}','message_ids':[i],'start':i,'end':i} for i in range(1,11)]
    example=[{'episode_id':'e1','target':'a'},{'episode_id':'e10','target':'b'}]
    splits, info=partition(episodes,example,{'10':{'status':'resolved','target_id':1}})
    assert info['allocation']['e1']==info['allocation']['e10']
    assert sum(len(v) for v in splits.values())==2


def test_examples_have_only_prior_context_and_self_target():
    rows=[msg(1,1700000000,text="明天什么时候"),msg(2,1700000010,"outgoing","下午两点"),
          msg(3,1700000020,text="好的，之后再安排")]
    ex=build(rows)[3][0]
    assert ex['target_message_ids']==[2]
    assert ex['context_message_ids']==[1]
    assert ex['target']=="下午两点"
    unreviewed = build(rows,reviews={'2':'approved'})
    assert not unreviewed[3]
    assert 'privacy_unreviewed_or_excluded' in unreviewed[6][0]['reasons']


def test_missing_attachment_and_orphan_pronoun_are_not_invented():
    t=1700000000
    data=build([msg(1,t,status='compressed_unresolved',eligible_text=False),
                msg(2,t+5,text='就这个东西'),msg(3,t+10,'outgoing','OK 我看一下')])
    assert data[3]==[] and data[5]['orphan_deictic_context']==1
    assert data[1][0]['status']=='compressed_unresolved'
    assert not build([msg(1,t,text='[捂脸]'),msg(2,t+10,'outgoing','已改')])[3]


def test_reference_metadata_is_local_and_checks_source_batch(tmp_path):
    source=tmp_path/'raw.jsonl'
    rows=[msg(1,1700000000,text='question'),msg(2,1700000010,'outgoing','reply',type=49),
          msg(3,1700000020,type=49,status='compressed_unresolved',eligible_text=False)]
    raw=[{'timestamp':r['timestamp'],'type':r['type'],'direction':r['direction'],'server_id':100+i,
          'content_encoding':'zstd' if i==2 else 'utf-8','content':
          '<msg><refermsg><svrid>100</svrid></refermsg></msg>' if i==1 else None} for i,r in enumerate(rows)]
    source.write_text(''.join(json.dumps(r)+'\n' for r in raw),encoding='utf-8')
    refs=references_from_source(source,rows)
    assert refs['2']=={'status':'resolved','target_id':1}
    assert refs['3']['status']=='missing_compressed_structured'
    assert '100' not in json.dumps(refs)
    raw[0]['content']='<msg><refermsg><svrid>101</svrid></refermsg></msg>'
    source.write_text(''.join(json.dumps(r)+'\n' for r in raw),encoding='utf-8')
    assert references_from_source(source,rows)['1']['status']=='not_prior'
    with pytest.raises(ValueError,match='does not match'):
        references_from_source(source,[{**rows[0],'timestamp':1700000001}])
