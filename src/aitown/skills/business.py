import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..models import Citation
from ..runtime.models import UserStatement
from .base import Skill

BEIJING = timezone(timedelta(hours=8))


class Evidence(BaseModel):
    message_id: int | None = None
    record_id: int | None = None
    subject: str
    timestamp: int
    direction: str
    speaker: str
    text: str

    def citation(self):
        source = 'message:'+str(self.message_id) if self.message_id is not None else 'statement:'+str(self.record_id)
        return Citation(source=source, chunk=0, text=self.text, score=1, subject=self.subject,
                        timestamp=self.timestamp, source_ids=[self.message_id] if self.message_id else [],
                        source_record_ids=[self.record_id] if self.record_id else [])


class MemoryQueryArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    topic: str = Field(default='', max_length=100)
    subject: str = Field(default='self', min_length=1, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    category: Literal['event', 'preference', 'long_term', 'plan', 'other_person'] | None = None
    date_field: Literal['recorded', 'occurred'] = 'recorded'
    limit: int = Field(default=3, ge=1, le=10)

    @model_validator(mode='after')
    def interval(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('开始日期不能晚于结束日期')
        return self


class MemoryEntry(BaseModel):
    memory_id: int
    episode_id: int | None
    subject: str
    category: str
    occurred_at: int | None
    recorded_at: int
    summary: str
    evidence: list[Evidence]


class MemoryQueryResult(BaseModel):
    entries: list[MemoryEntry]
    episode_ids: list[int]
    count: int
    truncated: bool = False
    status: Literal['ok', 'no_result']


def require_scope(context):
    if context is None or not context.scope:
        raise ValueError('请先显式指定 --scope 资料范围')
    return context.repository


def clean_evidence(row, subject):
    return Evidence(message_id=row['id'], subject=subject, timestamp=row['timestamp'],
                    direction=row['direction'], speaker=row['speaker_id'], text=row['content'])


def readable(row):
    return row['review_status'] == 'approved' and row['status'] in ('text', 'structured_text') and bool(row['content'].strip())


class MemoryQuery(Skill):
    name = 'memory_query'
    description = '按 topic/subject/start_date/end_date/category 查询已复核 train 记忆；日期为北京时间 YYYY-MM-DD，返回来源及 episode_ids，不执行 SQL。历史计划表示当时提过，不代表完成。'
    arguments = MemoryQueryArgs
    result_type = MemoryQueryResult

    def run(self, arguments):
        raise ValueError('个人记忆查询需要显式范围与应用库上下文')

    def run_with_context(self, arguments, context):
        repo = require_scope(context)
        start = int(datetime.combine(arguments.start_date, time.min, BEIJING).timestamp()) if arguments.start_date else None
        end = int(datetime.combine(arguments.end_date+timedelta(days=1), time.min, BEIJING).timestamp())-1 if arguments.end_date else None
        time_column = 'recorded_at' if arguments.date_field == 'recorded' else 'occurred_at'
        rows = repo.conn.execute("""SELECT * FROM memories
            WHERE scope=? AND subject=? AND split='train' AND basis='direct' AND status='confirmed'
            AND (? IS NULL OR category=?) AND (? IS NULL OR """+time_column+">=?) AND (? IS NULL OR "+time_column+"<=?) ORDER BY "+time_column+" DESC,id DESC",
            (context.scope, arguments.subject, arguments.category, arguments.category, start, start, end, end))
        entries = []
        for row in rows:
            sources = repo.conn.execute('SELECT r.* FROM memory_sources s JOIN message_refs r ON r.id=s.message_id WHERE s.memory_id=? ORDER BY r.timestamp,r.id', (row['id'],)).fetchall()
            if not sources or any(s['conversation_id'] != context.scope or not readable(s) for s in sources):
                continue
            if arguments.subject == 'self' and any(s['direction'] != 'outgoing' or s['speaker_id'] != 'self' for s in sources):
                continue
            payload = json.loads(row['payload'])
            summary = payload.get('summary') or payload.get('value') or ''
            body = summary+'\n'+'\n'.join(s['content'] for s in sources)
            tokens = ['学习', '复习', '课程', '作业', '实验', '考试'] if arguments.topic in ('学习', '学习计划') else [arguments.topic]
            if arguments.topic and not any(token in body for token in tokens):
                continue
            entries.append(MemoryEntry(memory_id=row['id'], episode_id=row['episode_id'], subject=row['subject'],
                category=row['category'], occurred_at=row['occurred_at'], recorded_at=row['recorded_at'], summary=summary,
                evidence=[clean_evidence(s, row['subject']) for s in sources]))
            if len(entries) > arguments.limit:
                break
        truncated = len(entries) > arguments.limit
        entries = entries[:arguments.limit]
        return MemoryQueryResult(entries=entries, episode_ids=list(dict.fromkeys(e.episode_id for e in entries if e.episode_id)),
                                 count=len(entries), truncated=truncated, status='ok' if entries else 'no_result')


class SummaryArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    episode_ids: list[int] = Field(min_length=1, max_length=10)
    format: Literal['points', 'timeline', 'markdown'] = 'points'
    max_messages: int = Field(default=12, ge=1, le=50)

    @model_validator(mode='after')
    def unique_ids(self):
        if any(type(i) is not int or i <= 0 for i in self.episode_ids) or len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError('事件 ID 必须为正整数且不重复')
        return self


class SummaryResult(BaseModel):
    content: str
    count: int
    episode_ids: list[int]
    evidence: list[Evidence]
    missing_parts: list[str]
    truncated: bool


class DialogueSummary(Skill):
    name = 'dialogue_summary'
    description = '对显式 episode_ids 清洗事件作摘录式 points/timeline/markdown 整理；保留说话人、计划、否定和缺失。episode_ids 可绑定 memory_query 的同名结果字段，count 为可读消息数。'
    arguments = SummaryArgs
    result_type = SummaryResult

    def run(self, arguments):
        raise ValueError('对话整理需要显式范围与应用库上下文')

    def run_with_context(self, arguments, context):
        repo = require_scope(context)
        evidence, lines, missing = [], [], []
        for eid in arguments.episode_ids:
            episode = repo.conn.execute('SELECT * FROM episodes WHERE id=? AND conversation_id=?', (eid, context.scope)).fetchone()
            # Do not reveal whether an out-of-scope event exists.
            if episode is None:
                raise ValueError('事件不存在或不在本次资料范围内')
            splits = repo.conn.execute('SELECT DISTINCT split FROM memories WHERE episode_id=?', (eid,)).fetchall()
            styles = repo.conn.execute('SELECT DISTINCT split FROM style_examples WHERE episode_id=?', (eid,)).fetchall()
            if any(r[0] != 'train' for r in [*splits, *styles]):
                raise ValueError('事件包含留出或未分配资料，不能用于运行时整理')
            rows = repo.conn.execute('SELECT r.* FROM episode_messages e JOIN message_refs r ON r.id=e.message_id WHERE e.episode_id=? ORDER BY e.ordinal', (eid,)).fetchall()
            lines.append(('## ' if arguments.format == 'markdown' else '')+f'事件 {eid}')
            if not rows:
                missing.append(f'事件 {eid} 没有可用消息')
            for row in rows:
                stamp = datetime.fromtimestamp(row['timestamp'], BEIJING).strftime('%Y-%m-%d %H:%M')
                if row['conversation_id'] != context.scope:
                    raise ValueError('事件来源超出本次资料范围')
                if not readable(row):
                    missing.append(f'事件 {eid} 消息 {row["id"]}：{row["status"]} 或未复核；未补造正文')
                    continue
                if len(evidence) >= arguments.max_messages:
                    missing.append(f'事件 {eid} 达到消息上限，后续正文省略')
                    break
                subject = 'self' if row['direction'] == 'outgoing' and row['speaker_id'] == 'self' else 'other:'+row['alias']
                source = clean_evidence(row, subject)
                evidence.append(source)
                # Quote complete source messages instead of changing tense or attribution.
                prefix = stamp+' ' if arguments.format in ('timeline', 'markdown') else ''
                body = row['content'].replace('\n', '\n  > ')
                lines.append(f'- {prefix}{subject} [{source.citation().ref}]\n  > {body}')
        if missing:
            lines.extend(['缺失/省略：', *missing])
        return SummaryResult(content='\n'.join(lines), count=len(evidence), episode_ids=arguments.episode_ids,
                             evidence=evidence, missing_parts=missing, truncated=any('上限' in s for s in missing))


class CorrectionArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    fact_id: int = Field(gt=0)
    value: str = Field(min_length=1, max_length=2000)


class CorrectionResult(BaseModel):
    previous_fact_id: int
    fact_id: int
    source_record_id: int
    evidence: list[Evidence]


def explicit_correction(question):
    match = re.fullmatch(r'(?:这条记忆不对[，,：:]?\s*)?纠正(?:事实|记忆)\s*(\d+)\s*(?:为)?[：:]\s*([^\n]+)', question.strip())
    return CorrectionArgs(fact_id=int(match[1]), value=match[2].strip()) if match else None


class MemoryCorrection(Skill):
    name = 'memory_correction'
    description = '可选写入：仅当用户本轮明确输入“纠正事实 ID：新值”时更新该范围本人事实，保留旧事实和新来源；参数 fact_id/value 必须与本轮原文一致。'
    arguments = CorrectionArgs
    result_type = CorrectionResult
    read_only = False

    def run(self, arguments):
        raise ValueError('纠正必须由运行时事务执行')

    def run_transaction_with_context(self, arguments, connection, context):
        repo = require_scope(context)
        if context.runtime is None or repo.path.resolve() != context.runtime.repo.path.resolve():
            raise ValueError('纠正需要检索库与运行库为同一应用库')
        explicit = explicit_correction(context.question)
        if explicit != arguments:
            raise ValueError('本轮用户未明确给出匹配的事实 ID 与纠正值')
        previous = connection.execute("SELECT * FROM facts WHERE id=? AND status='active' AND subject='self'", (arguments.fact_id,)).fetchone()
        if previous is None:
            raise ValueError('事实不存在、已被替代或主体不是本人')
        owners = connection.execute('SELECT s.agent_id FROM fact_statement_sources f JOIN source_records s ON s.id=f.record_id WHERE f.fact_id=?', (arguments.fact_id,)).fetchall()
        if any(row[0] != context.agent_id for row in owners):
            raise ValueError('不能纠正其他 Agent 的来源事实')
        statement = UserStatement(kind='user_correction', attribute=previous['attribute'], value=arguments.value,
                                  scope=context.scope, corrects_fact_id=arguments.fact_id)
        fact = context.runtime._record_statement(statement, context.question, context.session, context.agent_id)
        record = connection.execute('SELECT s.* FROM fact_statement_sources f JOIN source_records s ON s.id=f.record_id WHERE f.fact_id=?', (fact,)).fetchone()
        evidence = Evidence(record_id=record['id'], subject='self', timestamp=int(record['created']),
                            direction='outgoing', speaker='self', text=record['content'])
        return CorrectionResult(previous_fact_id=arguments.fact_id, fact_id=fact, source_record_id=record['id'], evidence=[evidence])


def citations(result):
    if not result.ok or not isinstance(result.data, dict):
        return []
    if result.name in ('question_search', 'question_get'):
        from ..question_bank.search import citations as question_citations
        return question_citations(result)
    raw = result.data.get('evidence', [])
    if result.name == 'memory_query':
        raw = [s for entry in result.data['entries'] for s in entry['evidence']]
    if result.name not in ('memory_query', 'dialogue_summary', 'memory_correction'):
        return []
    return [Evidence.model_validate(source).citation() for source in raw]


def current_citation(repository, scope, agent_id, source):
    if source.source.startswith('message:'):
        row = repository.conn.execute('SELECT * FROM message_refs WHERE id=? AND conversation_id=?',
                                     (source.source_ids[0] if len(source.source_ids) == 1 else -1, scope)).fetchone()
        subject = 'self' if row and row['direction'] == 'outgoing' and row['speaker_id'] == 'self' else 'other:'+row['alias'] if row else None
        interpreted = repository.conn.execute("SELECT 1 FROM memories m JOIN memory_sources ms ON ms.memory_id=m.id WHERE ms.message_id=? AND m.subject=? AND m.scope=? AND m.status='confirmed' AND m.split='train'", (row['id'], source.subject, scope)).fetchone() if row else None
        return bool(row and readable(row) and row['content'] == source.text and (subject == source.subject or interpreted))
    if source.source.startswith('statement:'):
        row = repository.conn.execute('SELECT s.* FROM source_records s JOIN fact_statement_sources fs ON fs.record_id=s.id JOIN facts f ON f.id=fs.fact_id WHERE s.id=? AND s.scope=? AND s.agent_id=? AND f.status=\'active\'',
            (source.source_record_ids[0] if len(source.source_record_ids) == 1 else -1, scope, agent_id)).fetchone()
        return bool(row and row['content'] == source.text and source.subject == 'self')
    return False
