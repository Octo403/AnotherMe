"""Live ChatGPT image selection on real originals, including a text-masked control."""
import json
import argparse
from pathlib import Path
import sys

from aitown.config import Settings
from aitown.question_bank.chatgpt import ChatGPTSimilarity
from aitown.question_bank.models import BankConfig, COURSE_ID, SearchArgs
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--production-only',action='store_true',help='保留已完成的遮字对照，只重新验证当前题库检索')
    args=parser.parse_args()
    settings=Settings.from_env(mode='demo')
    config=BankConfig(root=settings.question_bank_dir,embedding_dir=settings.local_embedding_dir,
        reranker_dir=settings.local_reranker_dir,similar_provider='codex_chatgpt',
        similar_model=settings.question_similar_model,similar_images=True)
    with BankRepository(config.database,readonly=True) as repo:
        rows=repo.rows(COURSE_ID)
        def question(sid,number):
            q=next(q for q in rows if q['source_id']==sid and q['number']==number)
            return repo.get([q['id']])[0]
        reference=question(879,'四');positive=question(776,'四')
        # Same course, but different mathematical objects and task types.
        candidates=[question(879,'六'),positive,question(879,'二')]
    report={'reference_id':reference['id'],'expected_id':positive['id'],
        'candidate_ids':[q['id'] for q in candidates],'runs':[],
        'assessment':'Codex 对照两张原题图确认：均考查函数集上的逐点偏序及最大元；两道计数题作为干扰。不是用户标注或全量准确率。'}
    target=Path('data/evaluation/question_bank/vision.json')
    if args.production_only:
        previous=json.loads(target.read_text(encoding='utf-8'))
        if previous['reference_id']!=reference['id'] or previous['expected_id']!=positive['id']:
            raise ValueError('对照样本已重解析，需要重新运行完整实验')
        report['runs']=previous['runs']
    def save():target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Mask texts, labels and types so the image trial cannot pass on OCR/topic clues.
    def masked(q):return {**q,'text':'文字提取缺失。','topics':[],'question_type':None}
    refs=[masked(reference)];masked_candidates=[masked(q) for q in candidates]
    for images in (() if args.production_only else (False,True)):
        selector=ChatGPTSimilarity(model=config.similar_model,image_root=config.root/'processed',use_images=images)
        selected,call=selector.select('找到与参考题数学条件和任务相似的题，不凑题。',refs,masked_candidates,1)
        report['runs'].append({'trial':'masked_images' if images else 'masked_text_only',
            'selection':selected.model_dump(),'call':call})
        save();print(json.dumps(report['runs'][-1],ensure_ascii=False),flush=True)
    result=QuestionBank(config).search(SearchArgs(query='函数集逐点比较定义偏序关系，求最大元存在的充要条件',
        reference_ids=[reference['id']],limit=2))
    report['production_search']=result.model_dump()
    report['checks']={'text_control_abstained':not report['runs'][0]['selection']['selected'],
        'image_trial_selected_expected':[q['id'] for q in report['runs'][1]['selection']['selected']]==[positive['id']],
        'images_sent':report['runs'][1]['call']['image_count']==4,
        'production_returned_expected':positive['id'] in result.question_ids,
        'production_used_images':any(c.get('input_mode')=='text+image' for c in result.model_calls),
        'source_images_preserved':all(q['images'] for q in result.results),'answers_hidden':all('answers' not in q for q in result.results),
        'annotated_notes_excluded':all(q['source_id']!=781 for q in result.results)}
    report['passed']=all(report['checks'].values());save()
    print(json.dumps({'checks':report['checks'],'passed':report['passed']},ensure_ascii=False))


if __name__=='__main__':main()
