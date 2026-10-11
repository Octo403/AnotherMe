"""Execute T7 synthetic first, then reviewed local evidence; reports stay private."""
import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np
import psutil

from aitown.retrieval import Retriever, SearchFilter
from aitown.retrieval.corpus import items
from aitown.retrieval.providers import LocalEmbedding
from aitown.retrieval.rerank import LocalReranker
from aitown.storage import Repository


CASES = [
    ("rain", "雨天通勤", "下雨时步行出门要携带雨伞，穿防水外套。雨伞可以挡雨，减少衣服被雨水打湿。", ["天气不好，步行上班怎么避免浑身湿透？", "雨天出门如何保持衣服干燥？"]),
    ("cold", "食物保存", "购买的蔬菜和鲜肉应放入冰箱冷藏。低温可延缓食物变质，熟食应及时装盒并冷藏。", ["买来的菜怎样才不容易坏？", "剩饭需要放在哪里保存？"]),
    ("library", "借阅须知", "图书馆借书期限为三十天，最多续借一次。请在借阅期限结束前办理续借，否则需归还图书。", ["书还没读完，可以晚点还吗？", "借来的书能保留多久？"]),
    ("wifi", "网络故障", "无线网络无法连接时，先检查路由器电源，然后重启路由器。仍无法上网时可检查网线并联系运营商。", ["家里突然上不了网该如何排查？", "无线连接断了先检查什么？"]),
    ("sleep", "作息建议", "改善睡眠可保持固定作息，睡前减少屏幕使用，下午避免大量咖啡因。卧室尽量安静、遮光。", ["晚上总睡不着，生活习惯可以怎么调整？", "睡前刷手机会影响休息吗？"]),
    ("backup", "数据备份", "重要文件要定期备份到不同设备。误删文件后先查看回收站，再检查备份副本，不要继续写入覆盖丢失数据。", ["不小心把文件删了怎么找回来？", "怎样避免电脑坏了就丢掉全部资料？"]),
    ("meeting", "会议预订", "团队会议室需要提前在日历系统预约，并注明人数和时间。预约冲突时选择其他时段或房间。", ["团队讨论需要房间，应先做什么？", "会议室已被占用时怎么办？"]),
    ("train", "乘车手续", "乘坐高铁应携带有效身份证件，提前到车站通过安检。检票结束后无法进入站台，请预留时间。", ["坐高铁应该提前准备哪些东西？", "为什么赶火车不能踩点到站？"]),
    ("plant", "植物养护", "盆栽根部不能长期积水。浇水前检查土壤是否干燥，花盆需有排水孔，避免过量浇水造成烂根。", ["花盆里的植物根烂了可能是什么原因？", "给盆栽浇水应该注意什么？"]),
    ("battery", "省电方法", "手机电量不足时可以降低屏幕亮度，关闭不使用的定位和后台应用，打开省电模式。", ["手机快没电了怎样撑到回家？", "手机耗电太快可以改哪些设置？"]),
    ("exam", "学习安排", "准备考试时先列出知识点和时间安排，再通过练习题检验薄弱环节。错题要分析原因，并间隔复习。", ["复习时间有限该怎样安排？", "总在同一种题目上出错怎么办？"]),
    ("exercise", "跑步准备", "开始跑步前应热身，初学者从短距离慢跑开始，逐渐增加运动量。结束后放松拉伸并补充水分。", ["第一次跑步要怎样开始？", "跑完步需要做些什么？"]),
    ("refund", "购物退货", "商品退货应查看退货期限，保存购买凭证和包装，通过订单页面申请。商家确认后按指定地址寄回。", ["买的东西不合适，怎么退回去？", "申请退货需要保留什么？"]),
    ("pdf", "文档转换", "Word 文档可通过文件菜单的另存为或导出功能生成 PDF。选择 PDF 格式后保存，便于分享固定排版文件。", ["编辑好的文字文档怎么变成PDF？", "发给别人的文件怎样保持排版不变？"]),
    ("appointment", "窗口办理", "办理证件前应查看材料清单，提前预约服务窗口，携带身份证明和所需材料，按预约时间前往。", ["到服务大厅办证应事先做哪些准备？", "办事窗口为什么要先预约？"]),
]
NEGATIVES = ["量子计算机纠错码是什么？", "火星轨道周期多长？", "法国中世纪王位继承制度是什么？"]


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def metrics(rows, field):
    ranks = [next((i+1 for i, key in enumerate(row[field]) if key == row['expected']), None) for row in rows]
    return {'recall_at_3': sum(r is not None and r <= 3 for r in ranks)/len(ranks),
            'recall_at_20': sum(r is not None for r in ranks)/len(ranks),
            'mrr': sum(1/r if r else 0 for r in ranks)/len(ranks)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', type=Path, default=Path('data/runtime/retrieval_models'))
    parser.add_argument('--database', type=Path, default=Path('data/runtime/application.sqlite3'))
    parser.add_argument('--output', type=Path, default=Path('data/private/t7/evaluation'))
    args = parser.parse_args()
    root = args.output/time.strftime('run-%Y%m%d-%H%M%S')
    root.mkdir(parents=True)
    process = psutil.Process()
    rss_start = process.memory_info().rss
    start = time.perf_counter()
    embedding = LocalEmbedding(args.models/'bge-small-zh-v1.5')
    reranker = LocalReranker(args.models/'bge-reranker-base')
    load_seconds = time.perf_counter()-start
    repo = Repository(root/'synthetic.sqlite3')
    expected = {}
    for name, title, text, _ in CASES:
        did = repo.add_document(name+'.md', '# '+title+'\n\n'+text)
        expected[name] = f'document:{did}:0'
    semantic = Retriever(repo, root/'synthetic-index', embedding, reranker, threshold=.45, rerank_threshold=.01)
    tfidf = Retriever(repo, root/'synthetic-index', threshold=.15)
    start = time.perf_counter()
    build = semantic.refresh('general')
    build_seconds = time.perf_counter()-start
    print('Synthetic model retrieval running', flush=True)
    rows = []
    for name, title, text, queries in CASES:
        for query in queries:
            base = tfidf.search(query, rerank=False)
            actual = semantic.search(query)
            rows.append({'query': query, 'expected': expected[name], 'tfidf': [h.item.key for h in base.coarse],
                         'coarse': [h.item.key for h in actual.coarse], 'ranked': [h.item.key for h in actual.ranked],
                         'selected': [h.item.key for h in actual.hits], 'status': actual.status, 'timings': actual.timings,
                         'scores': [{'key': h.item.key, 'score': h.score, 'rerank': h.rerank_score} for h in actual.ranked]})
    negative = [{'query': q, 'result': semantic.search(q).model_dump()} for q in NEGATIVES]
    rss_models = process.memory_info().rss
    semantic.close()
    restarted = Retriever(repo, root/'synthetic-index', embedding, reranker, threshold=.45, rerank_threshold=.01)
    restart = restarted.refresh('general')
    started = time.perf_counter()
    repo.upsert_document('rain.md', '# 雨天通勤\n\n雨天步行出门携带雨伞和防水外套，可以防止衣服被淋湿。')
    incremental = restarted.refresh('general')
    update_seconds = time.perf_counter()-started
    repo.delete_document(1)
    deletion = restarted.search(CASES[0][3][0])
    deleted_returned = any(h.item.entity_id == 1 for h in deletion.coarse)
    restarted.close()
    tfidf.close()
    repo.close()
    save(root/'synthetic_queries.json', {'known': rows, 'negative': negative})
    print('Synthetic complete; reviewed local corpus starts', flush=True)
    private_repo = Repository(args.database)
    private = Retriever(private_repo, Path('data/runtime/retrieval'), embedding, reranker, threshold=.45, rerank_threshold=.01)
    start = time.perf_counter()
    real_build = private.refresh()
    real_build_seconds = time.perf_counter()-start
    real_rows = []
    eligible = [item for item in items(private_repo, 'personal') if item.status == 'confirmed' and item.basis == 'direct' and item.category != 'plan']
    for item in eligible[:8]:
        # These are source-based smoke queries, not an independent recall benchmark.
        query = item.text.split('\n', 1)[-1][:100]
        result = private.search(query, SearchFilter(purpose='personal', subject=item.subject, scope=item.scopes[0]))
        real_rows.append({'expected': item.key, 'query': query, 'result': result.model_dump()})
    unknown = private.search('我曾经乘坐宇宙飞船去月球旅行的日期', SearchFilter(purpose='personal', subject='self', scope='c0067'))
    ambiguous = private.search('我的旅游经历', SearchFilter(purpose='personal', subject='self'))
    style = private.search('明天一起吃饭吗', SearchFilter(purpose='style', scope='c0067'))
    real_restart = private.refresh()
    private.close()
    private_repo.close()
    save(root/'private_queries.json', {'source_smoke': real_rows, 'unknown': unknown.model_dump(), 'ambiguous': ambiguous.model_dump(), 'style': style.model_dump()})
    totals = [r['timings']['coarse_seconds']+r['timings']['rerank_seconds'] for r in rows]
    report = {
        'run': str(root), 'embedding': embedding.name, 'dimension': embedding.dimension, 'reranker': reranker.name,
        'hardware': {'device': 'CPU', 'cpu': platform.processor(), 'logical_cpus': os.cpu_count(), 'threads': 4,
                     'physical_memory_bytes': psutil.virtual_memory().total, 'platform': platform.platform()},
        'load_seconds': load_seconds, 'synthetic_build': build, 'synthetic_build_seconds': build_seconds,
        'rss_start_bytes': rss_start, 'rss_after_models_and_queries_bytes': rss_models,
        'rss_final_bytes': process.memory_info().rss, 'known_queries': len(rows),
        'metrics': {f: metrics(rows, f) for f in ('tfidf', 'coarse', 'ranked', 'selected')},
        'negatives': {'total': len(negative), 'rejected': sum(r['result']['status'] == 'no_match' for r in negative)},
        'latency_seconds': {'median': float(np.median(totals)), 'p95': float(np.percentile(totals, 95)),
                            'coarse_mean': float(np.mean([r['timings']['coarse_seconds'] for r in rows])),
                            'rerank_mean': float(np.mean([r['timings']['rerank_seconds'] for r in rows]))},
        'restart': restart, 'incremental': incremental, 'incremental_seconds': update_seconds,
        'deleted_returned': deleted_returned, 'real_build': real_build, 'real_build_seconds': real_build_seconds,
        'real_restart': real_restart, 'real_source_smoke': {'total': len(real_rows),
            'expected_in_top4': sum(any(h['item']['key'] == r['expected'] for h in r['result']['hits']) for r in real_rows)},
        'personal_unknown_status': unknown.status, 'ambiguous_status': ambiguous.status, 'style_status': style.status,
        'thresholds': {'coarse': .45, 'rerank': .01},
    }
    save(root/'report.json', report)
    args.output.mkdir(exist_ok=True)
    save(args.output/'latest.json', report)
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)


if __name__ == '__main__':
    main()
