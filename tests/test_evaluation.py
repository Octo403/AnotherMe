import pytest

from aitown.evaluation import assess_reply, distribution, privacy_matches, ranking_metrics


def test_ranking_counts_groups_duplicates_and_separates_negatives():
    rows=[{'gold_groups':[['a','a2'],['b']], 'hits':['a','a','x','b']},
          {'gold_groups':[['z']], 'hits':[]},
          {'gold_groups':[], 'hits':['false']}, {'gold_groups':[], 'hits':[]}]
    metrics=ranking_metrics(rows)
    assert metrics['positives']==2 and metrics['negatives']==2
    assert metrics['recall']['1']==.25 and metrics['recall']['3']==.5
    assert metrics['mrr']==.5
    assert metrics['negative_false_hits']==1 and metrics['negative_empty']==1
    assert 0<metrics['ndcg']<.5
    assert ranking_metrics([])['recall']['1'] is None
    overlap=ranking_metrics([{'gold_groups':[['a'],['a']], 'hits':['a']}])
    assert overlap['ndcg']<=1


def test_latency_percentile_interpolates_and_does_not_invent_empty_results():
    assert distribution([])['p95'] is None
    value=distribution([1,2,3,100])
    assert value['p50']==2.5 and value['p95']==pytest.approx(85.45)
    assert distribution([4])['min']==4


def reply(**changes):
    value={'answer':'容量为999人','stop_reason':'answered','skills':[],
           'sources':[{'source':'document:1:0','chunk':0,'subject':None,'source_ids':[]}],
           'answer_source_refs':['document:1:0#0'],'model_calls':[],
           'references':[],'reviews':[{'deterministic_issues':[],'final_issues':[],'changed':False}]}
    return {**value,**changes}


def test_agent_self_review_cannot_override_independent_wrong_answer_or_subject():
    result=assess_reply({'expect':{'contains':[['7人']],'evidence':True}},reply())
    assert result['final_review_pass'] and not result['passed']
    assert not assess_reply({'expect':{'subject':'self'}},reply())['passed']
    assert not assess_reply({'expect':{'evidence':True}},reply(answer_source_refs=['invented#0']))['passed']
    assert not assess_reply({'expect':{'source_ids':[15]}},reply())['passed']


def test_unknown_checks_no_generation_and_real_tool_result():
    assert not assess_reply({'expect':{'stops':['no_evidence'],'no_generation':True}},reply())['passed']
    tool={'name':'calculator','ok':True,'data':{'value':42}}
    good=reply(answer='42',skills=[tool])
    assert assess_reply({'expect':{'skills':['calculator'],'value':42}},good)['passed']
    assert not assess_reply({'expect':{'value':999}},good)['passed']
    assert not assess_reply({'expect':{'no_generation':True}},reply(model_calls=[{'phase':'draft'}]))['passed']


def test_privacy_findings_return_categories_and_counts_without_values():
    found=privacy_matches('19900000000 fake@example.com sk-abcdefghijklmnopqrst')
    assert found=={'phone':1,'email':1,'api_key':1}
    assert '19900000000' not in str(found)
    assert not privacy_matches('2026-10-06 来源 ID 1758326400，已脱敏手机号 [PHONE]')
    assert not privacy_matches('rerank_score: 0.123456199901011234')
    assert privacy_matches('合成身份证12345619990101123X')=={'id_card':1}
