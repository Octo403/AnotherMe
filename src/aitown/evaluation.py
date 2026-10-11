"""Evaluation measurements; no model-generated scores stand in for owner ratings."""
import math
import re
from statistics import mean


def ranking_metrics(rows, *, field='hits', ks=(1, 3, 4)):
    """Macro recall over labelled source groups; empty gold is a negative case.

    A gold group may have several acceptable chunk keys. Retrieving one member
    satisfies that group, rather than inflating recall with duplicate chunks.
    """
    positives = [row for row in rows if row['gold_groups']]
    negatives = [row for row in rows if not row['gold_groups']]
    recalls = {str(k): [] for k in ks}
    reciprocal, ndcg = [], []
    for row in positives:
        keys = list(dict.fromkeys(row[field]))
        groups = [set(group) for group in row['gold_groups']]
        for k in ks:
            recalls[str(k)].append(sum(bool(group.intersection(keys[:k])) for group in groups)/len(groups))
        ranks = [next((i+1 for i, key in enumerate(keys) if key in group), None) for group in groups]
        found = [rank for rank in ranks if rank is not None]
        reciprocal.append(1/min(found) if found else 0)
        cutoff = max(ks)
        dcg = sum(1/math.log2(rank+1) for rank in set(ranks) if rank is not None and rank <= cutoff)
        ideal = sum(1/math.log2(i+2) for i in range(min(len(groups), cutoff)))
        ndcg.append(dcg/ideal if ideal else 0)
    return {'positives': len(positives), 'negatives': len(negatives),
            'recall': {k: mean(values) if values else None for k, values in recalls.items()},
            'mrr': mean(reciprocal) if reciprocal else None,
            'ndcg': mean(ndcg) if ndcg else None,
            'negative_false_hits': sum(bool(row[field]) for row in negatives),
            'negative_empty': sum(not row[field] for row in negatives)}


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'p50': None, 'p95': None, 'min': None, 'max': None, 'mean': None}
    def percentile(fraction):
        position = (len(values)-1)*fraction
        left = int(position)
        right = min(left+1, len(values)-1)
        return values[left]+(values[right]-values[left])*(position-left)
    return {'n': len(values), 'p50': percentile(.5), 'p95': percentile(.95),
            'min': values[0], 'max': values[-1], 'mean': mean(values)}


def assess_reply(case, reply):
    """Compare the delivered answer against independent expectations.

    The Agent's self-review is reported separately and cannot make this pass.
    Tokens/regex checks are conservative diagnostics, not semantic correctness.
    """
    expected = case.get('expect', {})
    checks = {'delivered': bool(reply.get('answer'))}
    if 'stops' in expected:
        checks['stop'] = reply['stop_reason'] in expected['stops']
    text = reply.get('answer', '')
    for index, alternatives in enumerate(expected.get('contains', [])):
        checks[f'answer_{index}'] = any(token in text for token in alternatives)
    for index, expression in enumerate(expected.get('patterns', [])):
        checks[f'meaning_{index}'] = bool(re.search(expression, text))
    if 'skills' in expected:
        checks['skill_order'] = [r['name'] for r in reply['skills']] == expected['skills']
    if 'value' in expected:
        checks['actual_value'] = any(r['ok'] and isinstance(r['data'], dict) and r['data'].get('value') == expected['value'] for r in reply['skills'])
    if expected.get('evidence'):
        valid = {source['source']+'#'+str(source['chunk']) for source in reply['sources']}
        checks['cited_evidence'] = bool(reply['answer_source_refs']) and set(reply['answer_source_refs']) <= valid
    if 'subject' in expected:
        checks['source_subject'] = bool(reply['sources']) and all(s['subject'] == expected['subject'] for s in reply['sources'])
    if 'selected' in expected:
        checks['resolved_reference'] = any(r['selected'] == expected['selected'] for r in reply['references'])
    if expected.get('no_generation'):
        checks['no_unsupported_generation'] = not any(c['phase'] in ('draft', 'review') for c in reply['model_calls'])
    if 'source_ids' in expected:
        returned = {message for source in reply['sources'] for message in source['source_ids']}
        checks['gold_source'] = bool(set(expected['source_ids']) & returned)
    checks['no_invalid_citation'] = not any(issue['code'] in ('missing_reference', 'subject_mismatch')
        for record in reply['reviews'] for issue in record['final_issues'])
    for index, forbidden in enumerate(expected.get('absent', [])):
        checks[f'absent_{index}'] = forbidden not in text
    return {'passed': all(checks.values()), 'checks': checks,
            'final_review_pass': bool(reply['reviews']) and not reply['reviews'][-1]['final_issues'],
            'draft_issues': [i['code'] for record in reply['reviews'] for i in record['deterministic_issues']],
            'final_issues': [i['code'] for record in reply['reviews'] for i in record['final_issues']],
            'review_changed': any(record['changed'] for record in reply['reviews'])}


def privacy_matches(text):
    """Potential sensitive formats only; returned findings omit matched values."""
    patterns = {'phone': r'(?<!\d)1[3-9]\d{9}(?!\d)',
                'id_card': r'(?<![\d.])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)',
                'email': r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
                'api_key': r'(?i)(?:sk-[A-Za-z0-9_-]{16,}|bearer\s+[A-Za-z0-9_-]{16,})'}
    return {name: len(re.findall(expression, text)) for name, expression in patterns.items() if re.search(expression, text)}
