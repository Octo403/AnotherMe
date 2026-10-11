"""Small reviewed-condition checks; these are not whole-paper OCR accuracy."""
import json
from pathlib import Path
import re
import sys


def main():
    folder=Path('data/evaluation/question_bank/paddle')
    paddle=json.loads((folder/'output/paddle.json').read_text(encoding='utf-8'))
    windows=json.loads((folder/'windows.json').read_text(encoding='utf-8'))
    conditions={
        '879-1-1':{'集合大小8':r'\|A\|=8','恰有3个等价类':r'恰有3个等价类'},
        '879-2-1':{'函数空间B^A':r'B\^\{?A\}?','逐点不等式':r'f\(x\)(?:≤|\\leq?)g\(x\)',
            '全称量词及定义域':r'(?:∀|\\forall)x(?:∈|\\in)A','最大元充要条件':r'存在最大元的充要条件'}}
    checks=[]
    for provider,items in (('windows',windows),('paddle',paddle['results'])):
        for item in items:
            text=re.sub(r'\s+','',item['text'])
            for condition,pattern in conditions.get(item['key'],{}).items():
                checks.append({'provider':provider,'sample':item['key'],'condition':condition,
                    'retained':bool(re.search(pattern,text))})
    report={'status':paddle['status'],'samples':len(paddle['results']),'physical_pages':4,
        'initialization_seconds':paddle['initialization_seconds'],
        'inference_seconds':sum(i['seconds'] for i in paddle['results']),
        'sample_seconds':{i['key']:i['seconds'] for i in paddle['results']},
        'versions':paddle['versions'],'checks':checks,
        'condition_retention':{p:{'retained':sum(c['retained'] for c in checks if c['provider']==p),
            'total':sum(c['provider']==p for c in checks)} for p in ('windows','paddle')},
        'manual_findings':[
            'Codex 对照原图：2020卷原题二和四的关键条件较Windows恢复明显。',
            '原题一仍把集合基数记号变成根号，原题三仍误识别基数与直积记号；不能直接入库。',
            '来源785混有手写答案、批注和图形，Paddle版面/文字仍有大量误识别，不能自动分离题答。',
            '来源786为清晰印刷填空页，完整数学条件和空白区域仍需逐题审校。'],
        'scope':'同一组6张图片；只检查两道已审校题的6个条件，不是字符准确率、全卷准确率或分题评测。未改题库。'}
    timing=folder/'windows-run.json'
    if timing.exists():report['windows_seconds']=json.loads(timing.read_text(encoding='utf-8'))['seconds']
    (folder/'assessment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('checks','manual_findings')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
