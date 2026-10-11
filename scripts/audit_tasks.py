"""Read-only verification of T1-T9 artifacts; reports counts, never private text."""
import argparse
import json
import sqlite3
import time
import html
import re
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def iter_lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('data/private/t10/audit'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'checks': {}, 'measurements': {},
              'scope': 'Current artifact consistency and original evaluation evidence; no new user ratings or full privacy review.'}
    checks, measurements = report['checks'], report['measurements']
    clean = Path('data/processed/wechat_clean_20261004_t2')
    cleaned_report = read(clean/'report.json')
    with readonly(clean/'messages.sqlite3') as conn:
        measurements['cleaned_sqlite_columns'] = [r['name'] for r in conn.execute('PRAGMA table_info(messages)')]
        count = conn.execute('SELECT count(*) FROM messages').fetchone()[0]
        statuses = dict(conn.execute('SELECT status,count(*) FROM messages GROUP BY status'))
        checks['T2_sqlite_counts_match_report'] = count == cleaned_report['source_records'] and statuses == cleaned_report['status_counts']
        jsonl_count, mismatches = 0, 0
        for item in iter_lines(clean/'messages.jsonl'):
            row = conn.execute('SELECT * FROM messages WHERE id=?', (item['id'],)).fetchone()
            fields = ('id', 'conversation_id', 'timestamp', 'direction', 'speaker_id', 'content', 'status', 'type', 'eligible_text')
            mismatches += row is None or any(row[field] != item[field] for field in fields)
            jsonl_count += 1
        checks['T2_entire_jsonl_matches_sqlite'] = jsonl_count == count and mismatches == 0
        html_errors, html_messages = 0, 0
        for conversation in conn.execute('SELECT conversation_id,count(*) FROM messages GROUP BY conversation_id').fetchall():
            folder = clean/'HTML'/conversation[0]
            cursor = conn.execute('SELECT content,status FROM messages WHERE conversation_id=? ORDER BY timestamp,id', (conversation[0],))
            for page in sorted(folder.glob('page-*.html')):
                text = page.read_text(encoding='utf-8')
                articles = re.findall(r'<article><small>(.*?)</small><pre>(.*?)</pre></article>', text, re.S)
                for header, body in articles:
                    row = cursor.fetchone()
                    html_errors += row is None or html.unescape(body) != row['content'] or not header.endswith(row['status'])
                    html_messages += 1
                for target in re.findall(r'href="([^"]+)"', text):
                    html_errors += not (page.parent/target).is_file()
            html_errors += cursor.fetchone() is not None
        checks['T2_all_html_messages_and_links_match_sqlite'] = html_errors == 0 and html_messages == count
        measurements['T2'] = {'records': count, 'status_counts': statuses, 'jsonl_mismatches': mismatches,
                              'html_messages': html_messages, 'html_errors': html_errors,
                              'html_pages': len(list((clean/'HTML').rglob('*.html'))),
                              'html_message_pages': len(list((clean/'HTML').rglob('page-*.html')))}
    data = Path('data/private/t3')
    normalized = {row['id']: row for row in lines(data/'normalized_messages.jsonl')}
    turns, episodes = lines(data/'turns.jsonl'), lines(data/'episodes.jsonl')
    target_ids = Counter(normalized.keys())
    checks['T3_turn_source_coverage'] = Counter(mid for row in turns for mid in row['message_ids']) == target_ids
    checks['T3_event_source_coverage'] = Counter(mid for row in episodes for mid in row['message_ids']) == target_ids
    checks['T3_merged_text_preserved'] = all(row['text'] == '\n'.join(normalized[mid]['content'] for mid in row['message_ids']) for row in turns)
    split_sources, split_episodes = {}, {}
    valid_targets, valid_order = True, True
    for split in ('train', 'dev', 'test'):
        samples = lines(data/(split+'.jsonl'))
        split_sources[split] = {mid for row in samples for mid in row['context_message_ids']+row['target_message_ids']}
        split_episodes[split] = {row['episode_id'] for row in samples}
        for row in samples:
            valid_targets &= all(normalized[mid]['direction'] == 'outgoing' and normalized[mid]['speaker_id'] == 'self' for mid in row['target_message_ids'])
            valid_order &= all((normalized[mid]['timestamp'], mid) < (normalized[row['target_message_ids'][0]]['timestamp'], row['target_message_ids'][0]) for mid in row['context_message_ids'])
    checks['T3_targets_self_and_prior_context'] = valid_targets and valid_order
    checks['T3_splits_disjoint'] = all(not split_sources[a] & split_sources[b] and not split_episodes[a] & split_episodes[b]
                                     for a, b in [('train', 'dev'), ('train', 'test'), ('dev', 'test')])
    measurements['T3'] = {'messages': len(normalized), 'turns': len(turns), 'episodes': len(episodes),
                          'splits': {split: len(lines(data/(split+'.jsonl'))) for split in split_sources}}
    with readonly('data/runtime/application.sqlite3') as conn:
        measurements['application_schema_version'] = conn.execute('PRAGMA user_version').fetchone()[0]
        measurements['application_counts'] = {table: conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
            for table in ('message_refs', 'episodes', 'style_examples', 'memories', 'facts', 'persona_traits', 'chat_turns')}
        checks['T4_foreign_keys'] = conn.execute('PRAGMA foreign_key_check').fetchall() == []
        app_messages = {r['source_id']: r for r in conn.execute('SELECT * FROM message_refs')}
        fields = ('conversation_id', 'timestamp', 'direction', 'speaker_id', 'content', 'status', 'type')
        checks['T4_all_1830_source_fields_match_T3'] = app_messages.keys() == normalized.keys() and all(
            all(row[field] == normalized[sid][field] for field in fields) for sid, row in app_messages.items())
        source_mismatches = 0
        for memory in conn.execute('SELECT * FROM memories'):
            payload = json.loads(memory['payload'])
            sources = {r['id']: r for r in conn.execute('SELECT r.* FROM memory_sources ms JOIN message_refs r ON r.id=ms.message_id WHERE ms.memory_id=?', (memory['id'],))}
            for evidence in payload['evidence']:
                row = sources.get(evidence['message_id'])
                source_mismatches += row is None or any(row[key] != evidence[target] for key, target in
                    [('batch_id', 'batch_id'), ('source_id', 'source_id'), ('timestamp', 'timestamp'),
                     ('direction', 'direction'), ('speaker_id', 'speaker_id'), ('content', 'excerpt')])
            if memory['subject'] == 'self' and memory['status'] == 'confirmed':
                source_mismatches += any(r['direction'] != 'outgoing' or r['speaker_id'] != 'self' for r in sources.values())
        checks['T5_all_memory_evidence_matches_sources'] = source_mismatches == 0
        measurements['T5_source_mismatches'] = source_mismatches
        styles, style_source_sets, target_errors = list(conn.execute('SELECT * FROM style_examples')), {}, 0
        for style in styles:
            rows = conn.execute('SELECT r.*,s.role,s.ordinal FROM style_sources s JOIN message_refs r ON r.id=s.message_id WHERE s.style_id=? ORDER BY s.ordinal', (style['id'],)).fetchall()
            style_source_sets.setdefault(style['split'], set()).update(row['id'] for row in rows)
            targets = [row for row in rows if row['role'] == 'target']
            target_errors += '\n'.join(row['content'] for row in targets) != style['target'] or any(
                row['direction'] != 'outgoing' or row['speaker_id'] != 'self' or row['review_status'] != 'approved' for row in targets)
        checks['T6_all_252_targets_match_self_sources'] = target_errors == 0
        checks['T6_train_sources_disjoint_from_holdout'] = not style_source_sets['train'] & (style_source_sets['dev'] | style_source_sets['test'])
        checks['T6_all_traits_have_sources'] = conn.execute('SELECT count(*) FROM persona_traits t WHERE NOT EXISTS(SELECT 1 FROM trait_sources s WHERE s.trait_id=t.id)').fetchone()[0] == 0
        measurements['T6_current_splits'] = dict(Counter(row['split'] for row in styles))
    ratings = read('data/private/t6/evaluation/summary.json')
    measurements['T6_original_owner_ratings'] = {'completed_ratings': ratings['completed_ratings'], 'complete': ratings['complete'],
        'similarity_means': {key: value['mean']['similarity'] for key, value in ratings['variants'].items()}}
    checks['T6_partial_evaluation_honestly_retained'] = ratings['completed_ratings'] == 26 and not ratings['complete']
    t7 = read('data/private/t7/evaluation/latest.json')
    measurements['T7_original_metrics'] = t7['metrics']
    checks['T7_restart_and_delete_evidence'] = t7['real_restart']['personal']['encoded'] == 0 and not t7['deleted_returned']
    t9_path = Path('data/private/t9/verification/run-20261006-193430')
    t9 = read(t9_path/'report.json')
    measurements['T9_original_checks'] = t9['checks']
    checks['T8_T9_original_six_turns_and_restart'] = t9['six_turns']['sixth_after_restart_contains_name_and_10'] and t9['six_turns']['other_session_empty']
    checks['T9_original_17_checks'] = len(t9['checks']) == 17 and all(t9['checks'].values())
    measurements['limitations'] = ['T2 privacy samples and format scans cannot establish complete anonymity.',
        'Compressed records remain unavailable; event segmentation and extraction still use finite rules.',
        'T6 owner ratings cover 26 answers, overall similarity was poor; no new subjective score.',
        'T7 rerank selected Recall@3 fell from 100% to 96.7%; only eight real source smoke queries.',
        'T8/T9 original live evidence is historical; current live regression is recorded separately for T10.']
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(args.output/'report.json'), 'checks': checks, 'measurements': measurements}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
