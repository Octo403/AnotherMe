import json
import re


def important(question, reply=None):
    # Negation, dates, changes and personal attribution are never paraphrased
    # away by the everyday-interaction summarizer.
    if re.search(r'不|没|否|从未|以前|现在|昨天|今天|明天|上个月|去年|\d{4}[-年]|我的|记住|纠正|我(?:是|住|去|喜欢|计划)', question):
        return True
    if reply is None:
        return True
    return reply.intent not in ('chat', 'skill') or any(call.name != 'calculator' for call in reply.tool_calls)


def summarize(rows):
    """Extract only older low-importance turns; keep their entire user wording.

    Drop verbose assistant boilerplate instead of rewriting facts. Sources remain
    independently queryable, and assistant text is explicitly model output.
    """
    entries = []
    for row in rows:
        if row['important'] or len(row['question']) > 120:
            continue
        entries.append({'turn_id': row['id'], 'created': row['created'], 'user': row['question'],
                        'assistant_output': row['answer'] if len(row['answer']) <= 80 else None,
                        'note': '历史模型/演示输出不作为事实；未保留的回答可按 turn_id 查看原文。'})
    return {'kind': 'extractive_daily_history', 'entries': entries, 'source_ids': [e['turn_id'] for e in entries]}


def encode(summary):
    return json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
