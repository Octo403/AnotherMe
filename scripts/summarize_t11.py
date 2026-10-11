"""Publish aggregate T11 measurements without private questions or evidence.

Latest records are selected per case from timestamped measurements, including
failures. This is a regression summary, not a newly measured independent run.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from aitown.evaluation import distribution, privacy_matches


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def summarize(root):
    trials=sorted((root/'measurements').glob('run-*'))
    latest={};style={};physical_calls=[];privacy_files=[];latest_retrieval=None
    for trial in trials:
        rows=trial/'graph_rows.json'
        if rows.exists():
            for row in read(rows):
                latest[row['id']]={**row,'measurement':str(trial)}
                physical_calls.extend(row.get('reply',{}).get('model_calls',[]))
            privacy_files.append(rows)
        retrieval=trial/'retrieval_report.json'
        if retrieval.exists():
            latest_retrieval=trial
            privacy_files.append(trial/'retrieval_rows.json')
        style_rows=trial/'style/trials_private.json'
        if style_rows.exists():
            for row in read(style_rows):
                key=(row['trial_id'].rsplit('-',1)[0],row['variant'])
                style[key]={**row,'measurement':str(trial)}
            physical_calls.extend(read(trial/'style_report.json').get('model_calls',[]))
            privacy_files.append(style_rows)
    categories={}
    for row in latest.values():
        entry=categories.setdefault(row['category'],{'n':0,'passed':0})
        entry['n']+=1;entry['passed']+=bool(row['assessment']['passed'])
    calls=[call for row in latest.values() for call in row.get('reply',{}).get('model_calls',[])]
    count_calls=lambda events: {'http_requests':sum(c.get('provider_request',False) for c in events),
        'input_tokens':sum(c.get('input_tokens') or 0 for c in events),'output_tokens':sum(c.get('output_tokens') or 0 for c in events)}
    cases=read(root/'cases.json')['cases']
    failures=[{'id':row['id'],'failed_checks':[name for name,ok in row['assessment']['checks'].items() if ok is False],
               'measurement':row['measurement']} for row in latest.values() if not row['assessment']['passed']]
    retrieval=read(latest_retrieval/'retrieval_report.json')
    misses=[{key:row[key] for key in ('id','set','diagnosis')} for row in read(latest_retrieval/'retrieval_rows.json') if row.get('diagnosis')]
    privacy_findings={str(path):privacy_matches(path.read_text(encoding='utf-8')) for path in privacy_files}
    canaries=[str(path) for path in privacy_files if 'KEEPOUT-T11-98314' in path.read_text(encoding='utf-8')]
    findings={path:value for path,value in privacy_findings.items() if value}
    owner=read(Path('data/private/t11/owner_feedback/ratings.json'))
    owner_cases=[case for case in owner['cases'] if any(answer.get('comment') for answer in case['answers'])]
    result={'dataset':{'cases':len(cases),'categories':dict(Counter(case['category'] for case in cases)),
        'public_synthetic_cases':sum(case['database']=='synthetic' for case in cases),
        'real_cases':sum(case['database']=='real' for case in cases),'label_author':'Codex source review; owner annotations are separate',
        'gold_corrections':str(root/'gold_corrections.json')},
        'retrieval':{'measurement':str(latest_retrieval),'selection':read(latest_retrieval/'selection.json'),'report':retrieval,'misses':misses},
        'graph':{'responses':len(latest),'passed':sum(row['assessment']['passed'] for row in latest.values()),'categories':categories,
            'failures':failures,'provenance':{key:row['measurement'] for key,row in latest.items()},
            'latency_seconds':distribution([row['seconds'] for row in latest.values() if row['category']!='recovery']),
            'selected_result_cost':count_calls(calls),
            'candidate_plan_errors':sum(any(error['phase']=='intent' for error in row['reply']['errors']) for row in latest.values() if 'reply' in row),
            'unknown_no_generation':sum(row['reply']['stop_reason']=='no_evidence' and not any(call['phase'] in ('draft','review') for call in row['reply']['model_calls']) for row in latest.values() if 'reply' in row),
            'note':'Latest actual record per case; includes affected-case retests and corrected labels. Not a fresh single full run. Latency excludes restart/recovery.'},
        'style':{'latest_answers':len(style),'unique_questions':len({key[0] for key in style}),
            'owner_reviewed_questions':len(owner_cases),'numeric_scored_answers':sum(all(answer.get(key) is not None for key in ('naturalness','similarity','scene_fit')) for case in owner['cases'] for answer in case['answers']),
            'feedback_source':'data/private/t11/owner_feedback/feedback_source.json',
            'diagnostics':{variant:{'n':len(rows),'chars':distribution([len(row['answer']) for row in rows]),
                'terminal_full_stops':sum(row['answer'].endswith(('。','.')) for row in rows)}
                for variant in ('plain','profile','profile_style') for rows in [[row for key,row in style.items() if key[1]==variant]]},
            'provenance':{case+'-'+variant:row['measurement'] for (case,variant),row in style.items()},
            'note':'Post-feedback diagnostic answers are not new owner ratings; original T6 records unchanged.'},
        'privacy':{'scanned_measurement_files':len(privacy_files),'format_findings':findings,'canary_files':canaries,
            'scope_note':'Detailed private outputs, prompts, evidence citations and diagnostics; no live UI or fine-tuning implementation.',
            'latest_full_scan':next((read(trial/'privacy_report.json') for trial in reversed(trials) if (trial/'privacy_report.json').exists()),None)},
        'experimental_cost':{**count_calls(physical_calls),'note':'Recorded graph and style calls across all retained T11 measurements, including retests and failures. Embedding/reranker CPU work is separate. Local provider only; no monetary estimate.'}}
    save(root/'summary_private.json',result)
    # Aggregates, identifiers and paths only; do not publish prompts or texts.
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('data/evaluation/t11_summary.json'))
    args=parser.parse_args()
    report=summarize(args.run_dir)
    save(args.output,report)
    print(json.dumps({'dataset':report['dataset']['cases'],'responses':report['graph']['responses'],'passed':report['graph']['passed'],
        'unknown_no_generation':report['graph']['unknown_no_generation'],'latency':report['graph']['latency_seconds'],
        'cost':report['graph']['selected_result_cost'],'experimental_cost':report['experimental_cost'],'candidate_errors':report['graph']['candidate_plan_errors'],
        'style':report['style']['diagnostics'],'privacy_scanned':report['privacy']['scanned_measurement_files'],'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':main()
