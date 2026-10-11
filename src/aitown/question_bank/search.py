"""Read-only question access; exact filters precede character retrieval."""
import re
import sqlite3
import time

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .models import BankConfig, GetArgs, QuestionResult, SearchArgs, StatusResult
from .prepare import TOPICS
from .repository import BankRepository


class QuestionBank:
    def __init__(self, config: BankConfig):
        self.config = config
        from .semantic import SemanticSearch
        self.semantic = SemanticSearch(config)
        self.similarity = None

    def courses(self):
        if self.config.database.is_file():
            with BankRepository(self.config.database,readonly=True) as repo:
                values=repo.courses()
                if values:
                    return values
        from .models import COURSE_ID,COURSE_NAME
        return [{'id':COURSE_ID,'name':COURSE_NAME,'path':'public/resources/'+COURSE_NAME,'files':0,'questions':0}]

    def resolve_course(self, value):
        value=value.strip() if isinstance(value,str) else ''
        return next((c['id'] for c in self.courses() if value in (c['id'],c['name'])),'')

    def status(self, course):
        if not self.config.database.is_file():
            return StatusResult(status='needs_preparation', course=course)
        with BankRepository(self.config.database, readonly=True) as repo:
            from .files import source_summary
            from .jobs import pid_alive
            coverage = repo.coverage(course)
            jobs = [dict(r) for r in repo.conn.execute('SELECT * FROM jobs WHERE course_id=? ORDER BY id DESC LIMIT 5', (repo.course_id(course),))]
            for job in jobs:
                import json
                for key in ('source_ids', 'done_ids'):
                    job[key] = json.loads(job[key])
                if (job['status']=='running' or (job['status']=='queued' and time.time()-job['updated_at']>=30)) and not pid_alive(job['owner_pid']):
                    job['status']='interrupted'
                    job['error']='后台进程已结束，可点击“继续收集”处理未完成资料。'
            question_counts=dict(repo.conn.execute('SELECT source_id,count(*) FROM questions GROUP BY source_id'))
            files=[source_summary(self.config.root,s,question_counts.get(s['id'],0)) for s in repo.sources(course)]
            states = coverage['states']
            processed=any(states.get(k,0) for k in ('ready','partial','needs_review','unsupported','expanded'))
            status = 'needs_preparation' if not coverage['questions'] and not processed else 'partial' if not coverage['questions'] or any(k not in ('ready', 'skipped', 'expanded') for k in states) else 'ready'
            return StatusResult(status=status, course=repo.course_id(course), coverage=coverage, jobs=jobs, files=files)

    def search(self, args: SearchArgs):
        status = self.status(args.course)
        if status.status == 'needs_preparation':
            active=next((job for job in status.jobs if job['status'] in ('queued','running')),None)
            if active:
                return QuestionResult(status='preparing',coverage={**status.coverage,'preparation':{
                    'id':active['id'],'status':active['status'],'stage':active['stage'],
                    'done':len(active['done_ids']),'total':len(active['source_ids'])}})
            return QuestionResult(status=status.status, coverage=status.coverage)
        with BankRepository(self.config.database, readonly=True) as repo:
            references=repo.get(args.reference_ids)
            reference_occurrences = {qid for q in references for qid in q.get('occurrence_ids',[q['id']])}
            excluded_occurrences = {qid for q in repo.get(args.exclude_ids) for qid in q.get('occurrence_ids',[q['id']])}
            if args.reference_ids and (not set(args.reference_ids)<=reference_occurrences or any(q['course_id']!=repo.course_id(args.course) for q in references)):
                return QuestionResult(status='invalid_ids',coverage=status.coverage,missing_ids=args.reference_ids)
            candidates = [q for q in repo.rows(args.course, year=args.year, exam=args.exam,
                category=args.category, question_type=args.question_type)
                if not set(q.get('occurrence_ids',[q['id']])) & {*args.exclude_ids,*args.reference_ids,*reference_occurrences,*excluded_occurrences}]
            if not candidates:
                return QuestionResult(status='no_result', coverage=status.coverage)
            course=next((c for c in self.courses() if c['id']==repo.course_id(args.course)),None)
            query=args.query
            if course:
                query=re.sub('|'.join(re.escape(v) for v in (course['name'],course['id'])), ' ',query)
            query = re.sub(r'先别给答案|不要答案|本轮要求|我说的是|我要的是|帮我|请|不限年份|不限题型|不限考试|只要|只看|再找|找|检索|筛选|题库|[一二两三四五六七八九十\d]+道|证明题|计算题|判断题|选择题|构造题|期末|期中|往年|有关|涉及|有哪些|有什么|有哪几题|有哪几道|给我看看|看看|的|题目|一些', ' ', query)
            if '不限年份' in args.query:
                query = re.sub(r'(?:19|20)\d{2}年?', ' ', query)
            if args.year:
                query = query.replace(str(args.year), ' ')
            browse = not re.search(r'[\u4e00-\u9fffA-Za-z0-9]{2}', query)
            from .models import COURSE_ID
            topics=TOPICS if repo.course_id(args.course)==COURSE_ID else {}
            wanted = [topic for topic, words in topics.items() if topic in query or any(word.lower() in query.lower() for word in words if len(word) > 1)]
            if '关系' in wanted and any(t in wanted for t in ('等价关系','偏序')):
                wanted.remove('关系')
            query += ' '+' '.join(wanted)
            documents = [q['text']+' '+(' '.join(q['topics'])+' ')*3 for q in candidates]
            # A user names the subject area; actual stems often say only
            # "排列" or "错排". These literal terms still matter in long stems.
            aliases=['排列','组合','错排'] if repo.course_id(args.course)==COURSE_ID and '排列组合' in query else []
            try:
                vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 4), sublinear_tf=True)
                vectors = vectorizer.fit_transform(documents)
                scores = np.asarray((vectors @ vectorizer.transform([query]).T).toarray()).ravel()
            except ValueError:
                scores = np.zeros(len(candidates))
            order = sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i]['id']))
            hits = list(range(len(candidates))) if browse else [i for i in order
                if (scores[i]>=.06 or any(term in candidates[i]['text'] for term in aliases))
                and (not wanted or set(wanted) & set(candidates[i]['topics']))]
            warnings,model_calls,method=[],[], 'filters' if browse else 'keyword'
            if self.config.embedding_dir and not browse and self.config.similar_provider != 'api':
                try:
                    semantic=self.semantic.scores(query,candidates)
                    semantic_order=sorted(range(len(candidates)),key=lambda i:-semantic.get(candidates[i]['id'],-1))
                    semantic_hits=[i for i in semantic_order if semantic.get(candidates[i]['id'],0)>=.50 and (not wanted or set(wanted)&set(candidates[i]['topics']))]
                    pool=list(dict.fromkeys([*semantic_hits[:20],*hits[:20]]))
                    hits=sorted(pool,key=lambda i:-(1/(60+semantic_order.index(i))+1/(60+order.index(i))))
                    method='bge+keyword'
                    if self.config.reranker_dir and hits:
                        ranked=self.semantic.rerank(query,[candidates[i] for i in hits])
                        hits=[i for _,i in sorted(zip(ranked,hits),reverse=True)]
                        method+='+bge_rerank'
                except Exception as exc:
                    warnings.append(f'本地 BGE 检索不可用（{type(exc).__name__}），本次使用关键词回退；请检查模型或刷新题库索引。')
                    method='keyword_fallback'
            reasons={}
            if self.config.similar_provider == 'api':
                try:
                    from .api_model import QuestionAPI
                    # OCR topic tags may be incomplete: the API also sees the
                    # other candidates that satisfy all exact user filters.
                    pool = list(dict.fromkeys([*hits, *order]))[:40]
                    shortlist = repo.get([candidates[i]['id'] for i in pool])
                    selection, call = QuestionAPI(self.config.api, self.config.root/'processed').select(
                        args.query, references, shortlist, args.limit)
                    lookup = {candidates[i]['id']: i for i in pool}
                    hits = [lookup[s.id] for s in selection.selected]
                    reasons = {s.id: s.reason for s in selection.selected}
                    model_calls.append(call)
                    method += '+api_selection'
                    if selection.missing:
                        warnings.append(selection.missing)
                    if len(pool) < len(candidates):
                        warnings.append(f'本轮模型核对了 {len(pool)}/{len(candidates)} 道筛选候选，可补充知识点缩小范围。')
                except Exception as exc:
                    warnings.append(f'在线模型筛题失败（{type(exc).__name__}），以下仅为本地筛选结果。')
                    model_calls.append({'provider': self.config.api.provider, 'model': self.config.api.model,
                        'status': 'failed', 'phase': 'question_selection', 'error_type': type(exc).__name__})
            if self.config.similar_provider=='codex_chatgpt':
                try:
                    from .chatgpt import ChatGPTSimilarity
                    self.similarity=self.similarity or ChatGPTSimilarity(model=self.config.similar_model,
                        image_root=self.config.root/'processed',use_images=self.config.similar_images,
                        cli_path=self.config.codex_path)
                    # Exact course/year/type filters still apply. Let the model
                    # check plausible candidates even when OCR misses keywords.
                    pool=list(dict.fromkeys([*hits,*order]))[:20]
                    shortlist=repo.get([candidates[i]['id'] for i in pool])
                    selection,call=self.similarity.select(args.query,references,shortlist,args.limit)
                    lookup={candidates[i]['id']:i for i in pool}
                    hits=[lookup[s.id] for s in selection.selected]
                    reasons={s.id:s.reason for s in selection.selected}
                    model_calls.append(call);method+='+chatgpt_similar' if references else '+chatgpt_selection'
                    if selection.missing:warnings.append(selection.missing)
                    if call.get('candidate_count',len(pool))<len(candidates):
                        warnings.append(f"本轮 ChatGPT 核对了 {call.get('candidate_count',len(pool))}/{len(candidates)} 道筛选候选，可补充知识点缩小范围。")
                except Exception as exc:
                    warnings.append(f'ChatGPT 第二轮筛题失败（{type(exc).__name__}）；以下为本地检索候选，不冒充模型判断。')
                    model_calls.append({'provider':'codex_chatgpt','status':'failed','error_type':type(exc).__name__})
            ids = [candidates[i]['id'] for i in hits[:args.limit]]
            current = {q['id']: q for q in repo.get(ids)}
            results = []
            for i in hits[:args.limit]:
                q = current.get(candidates[i]['id'])
                if q is not None:
                    q.update(score=round(float(scores[i]), 5), match_reason=reasons.get(q['id']) or
                        ('符合筛选条件'+('：'+args.question_type+'题' if args.question_type else '') if browse else
                         '命中知识点：'+'、'.join(sorted(set(wanted) & set(q['topics']))) if wanted else '题干字符匹配'))
                    results.append(q)
            return QuestionResult(status='ok' if results else 'no_result', question_ids=[q['id'] for q in results],
                results=results, count=len(results), coverage=status.coverage, truncated=len(hits) > args.limit,
                method=method,warnings=warnings,model_calls=model_calls)

    def get(self, args: GetArgs):
        if not self.config.database.is_file():
            return QuestionResult(status='needs_preparation', missing_ids=args.question_ids)
        with BankRepository(self.config.database, readonly=True) as repo:
            results = repo.get(args.question_ids, args.include_answer)
            ids = [q['id'] for q in results]
            found = {qid for q in results for qid in q.get('occurrence_ids',[q['id']])}
            missing = [qid for qid in args.question_ids if qid not in found]
            return QuestionResult(status='partial' if results and missing else 'ok' if results else 'invalid_ids',
                results=results, question_ids=ids, count=len(results), missing_ids=missing,
                coverage={cid: repo.coverage(cid) for cid in {q['course_id'] for q in results}})

    def current_citation(self, source):
        try:
            qid = int(source.source.split(':')[1])
            result = self.get(GetArgs(question_ids=[qid]))
            return bool(result.results and result.results[0]['text'] == source.text and result.results[0]['url'] == source.external_url and source.chunk == 0)
        except (ValueError, sqlite3.Error):
            return False


def citations(result):
    from ..models import Citation
    if not result.ok or result.name not in ('question_search', 'question_get'):
        return []
    return [Citation(source=f"question:{q['id']}", chunk=0, text=q['text'], score=q.get('score', 1),
        subject='general', external_url=q['url'], locations=q['pages'],
        location_kind='paragraph' if (q['member'] or q['path']).lower().endswith('.docx') else 'line' if (q['member'] or q['path']).lower().endswith(('.txt', '.md')) else 'page',
        question_id=q['id']) for q in result.data['results']]
