"""Separate SQLite, file transactions and monotonically allocated question IDs."""
import json
import re
import sqlite3
import time
import filecmp
from itertools import combinations
from pathlib import Path

from .models import COURSE_ID, COURSE_NAME, ParsedSource, Source


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class BankRepository:
    def __init__(self, database: Path, *, readonly=False):
        self.path = Path(database)
        self.readonly = readonly
        if readonly:
            self.conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        if not readonly:
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS courses(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                    checked_at REAL, discovery_error TEXT);
                CREATE TABLE IF NOT EXISTS sources(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT NOT NULL REFERENCES courses(id),
                    repository TEXT NOT NULL, path TEXT NOT NULL, member TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL, size INTEGER, category_hint TEXT, year_hint INTEGER, exam_hint TEXT,
                    category TEXT NOT NULL DEFAULT 'unknown', year INTEGER, exam TEXT,
                    local_path TEXT, discovered_at REAL NOT NULL, checked_at REAL, processed_at REAL,
                    status TEXT NOT NULL DEFAULT 'discovered', error TEXT, notes TEXT NOT NULL DEFAULT '[]',
                    remote_removed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(course_id,repository,path,member));
                CREATE TABLE IF NOT EXISTS pages(
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, image TEXT, location_kind TEXT,
                    printed_label TEXT, PRIMARY KEY(source_id,ordinal));
                CREATE TABLE IF NOT EXISTS rendered_pages(
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, image TEXT, location_kind TEXT,
                    printed_label TEXT, PRIMARY KEY(source_id,ordinal));
                CREATE TABLE IF NOT EXISTS questions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    number TEXT NOT NULL, text TEXT NOT NULL, normalized TEXT NOT NULL,
                    pages TEXT NOT NULL, subquestions TEXT NOT NULL, topics TEXT NOT NULL,
                    question_type TEXT, images TEXT NOT NULL, missing TEXT NOT NULL,
                    UNIQUE(source_id,number));
                CREATE TABLE IF NOT EXISTS answers(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    text TEXT NOT NULL, locations TEXT NOT NULL, basis TEXT NOT NULL,
                    UNIQUE(question_id,source_id));
                CREATE TABLE IF NOT EXISTS question_duplicates(
                    first_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    second_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    PRIMARY KEY(first_id,second_id), CHECK(first_id<second_id));
                CREATE TABLE IF NOT EXISTS jobs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT NOT NULL REFERENCES courses(id),
                    status TEXT NOT NULL, stage TEXT NOT NULL, source_ids TEXT NOT NULL,
                    done_ids TEXT NOT NULL DEFAULT '[]', current_source INTEGER, error TEXT,
                    owner_pid INTEGER, updated_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_course_job ON jobs(course_id)
                    WHERE status IN ('queued','running');
            ''')
            if 'options' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(jobs)')}:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN options TEXT NOT NULL DEFAULT '{}'")
            if 'missing' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(answers)')}:
                self.conn.execute("ALTER TABLE answers ADD COLUMN missing TEXT NOT NULL DEFAULT '[]'")
            if 'image_regions' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(questions)')}:
                self.conn.execute("ALTER TABLE questions ADD COLUMN image_regions TEXT NOT NULL DEFAULT '[]'")
            if 'rendered_text' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(questions)')}:
                self.conn.execute("ALTER TABLE questions ADD COLUMN rendered_text TEXT NOT NULL DEFAULT ''")
            if 'dedup_layout' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(questions)')}:
                self.conn.execute("ALTER TABLE questions ADD COLUMN dedup_layout TEXT NOT NULL DEFAULT ''")
            if 'raw_deleted_at' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(sources)')}:
                self.conn.execute('ALTER TABLE sources ADD COLUMN raw_deleted_at REAL')
            if 'resolved_url' not in {r['name'] for r in self.conn.execute('PRAGMA table_info(sources)')}:
                self.conn.execute('ALTER TABLE sources ADD COLUMN resolved_url TEXT')
            self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def course_id(self, course):
        row = self.conn.execute('SELECT id FROM courses WHERE id=? OR name=?', (course, course)).fetchone()
        return row['id'] if row else COURSE_ID if course in (COURSE_ID, COURSE_NAME) else course

    def courses(self):
        rows=self.conn.execute("SELECT c.id,c.name,c.path,count(s.id) AS files FROM courses c "
            "LEFT JOIN sources s ON s.course_id=c.id AND s.member='' AND s.remote_removed=0 "
            "GROUP BY c.id HAVING files>0 OR c.id=? ORDER BY c.name",(COURSE_ID,)).fetchall()
        return [{**dict(row),'questions':len(self.rows(row['id']))} for row in rows]

    def save_discovery(self, courses, sources):
        now = time.time()
        with self.conn:
            for course in courses:
                self.conn.execute('INSERT INTO courses(id,name,path,checked_at) VALUES(?,?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET name=excluded.name,path=excluded.path,checked_at=excluded.checked_at,discovery_error=NULL',
                    (course['id'], course['name'], course['path'], now))
            for source in sources:
                self.save_source(source)
            # A complete successful directory refresh is required before marking removals.
            seen = {(s.course_id, s.repository, s.path) for s in sources}
            refreshed_courses = {c['id'] for c in courses}
            refreshed_repositories = {s.repository for s in sources}
            for row in self.conn.execute("SELECT * FROM sources WHERE member='' AND remote_removed=0").fetchall():
                if row['course_id'] in refreshed_courses and row['repository'] in refreshed_repositories and (row['course_id'], row['repository'], row['path']) not in seen:
                    self.conn.execute('UPDATE sources SET remote_removed=1 WHERE course_id=? AND repository=? AND path=?',
                        (row['course_id'],row['repository'],row['path']))

    def save_source(self, source: Source):
        self.conn.execute('''INSERT INTO sources(course_id,repository,path,member,url,size,category_hint,year_hint,exam_hint,discovered_at,checked_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(course_id,repository,path,member) DO UPDATE SET
            url=excluded.url,size=excluded.size,checked_at=excluded.checked_at,remote_removed=0''',
            (source.course_id, source.repository, source.path, source.member, source.url, source.size,
             source.category_hint, source.year_hint, source.exam_hint, time.time(), time.time()))
        return self.conn.execute('SELECT id FROM sources WHERE course_id=? AND repository=? AND path=? AND member=?',
            (source.course_id, source.repository, source.path, source.member)).fetchone()[0]

    def source(self, source_id):
        row = self.conn.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if row is None:
            raise ValueError('来源 ID 不存在')
        return dict(row)

    def sources(self, course):
        return [dict(r) for r in self.conn.execute('SELECT * FROM sources WHERE course_id=? ORDER BY id', (self.course_id(course),))]

    def set_source(self, source_id, **fields):
        allowed = {'status', 'error', 'local_path', 'processed_at', 'notes', 'raw_deleted_at'}
        if not fields.keys() <= allowed:
            raise ValueError('未知来源字段')
        with self.conn:
            self.conn.execute('UPDATE sources SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(), source_id))

    def replace_parsed(self, source_id, parsed: ParsedSource):
        with self.conn:
            # Replacing an answer source also invalidates links to other sources' questions.
            self.conn.execute('DELETE FROM answers WHERE source_id=?', (source_id,))
            self.conn.execute('DELETE FROM questions WHERE source_id=?', (source_id,))
            self.conn.execute('DELETE FROM pages WHERE source_id=?', (source_id,))
            self.conn.execute('DELETE FROM rendered_pages WHERE source_id=?', (source_id,))
            for page in parsed.pages:
                self.conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',
                    (source_id, page.ordinal, page.text, page.image, page.location_kind, page.printed_label))
            for page in parsed.rendered_pages:
                self.conn.execute('INSERT INTO rendered_pages VALUES(?,?,?,?,?,?)',
                    (source_id, page.ordinal, page.text, page.image, page.location_kind, page.printed_label))
            for q in parsed.questions:
                self.conn.execute('''INSERT INTO questions(source_id,number,text,normalized,pages,subquestions,topics,question_type,images,missing,image_regions,rendered_text,dedup_layout)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (source_id, q.number, q.text, ''.join(q.text.split()).lower(), encode(q.pages),
                    encode(q.subquestions), encode(q.topics), q.question_type, encode(q.images), encode(q.missing),
                    encode([r.model_dump() for r in q.image_regions]),q.rendered_text,q.dedup_layout))
            self.conn.execute('UPDATE sources SET category=?,year=?,exam=?,status=?,notes=?,error=NULL,processed_at=? WHERE id=?',
                (parsed.category, parsed.year, parsed.exam, parsed.status, encode(parsed.notes), time.time(), source_id))

    def pair_answers(self, question_source_id, answer_source_id, pairs):
        """Replace this source pair atomically after checking current stems and locations."""
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            question_source, answer_source = self.source(question_source_id), self.source(answer_source_id)
            if question_source['course_id'] != answer_source['course_id']:
                raise ValueError('题目与答案不属于同一课程')
            if any(s['remote_removed'] or s['status'] not in ('ready','partial') for s in (question_source,answer_source)):
                raise ValueError('题目或答案来源当前不可用，请重新处理')
            if answer_source['category'] != 'answer':
                raise ValueError('配对来源必须是独立保存的答案资料')
            for field in ('year','exam'):
                if question_source[field] is not None and answer_source[field] is not None and question_source[field] != answer_source[field]:
                    raise ValueError('题目与答案的年份或考试类别不一致')
            if len({p.question_number for p in pairs}) != len(pairs):
                raise ValueError('配对文件含重复题号')
            values=[]
            for pair in pairs:
                q = self.conn.execute('SELECT id,text FROM questions WHERE source_id=? AND number=?',
                    (question_source_id,pair.question_number)).fetchone()
                if q is None or ''.join(q['text'].split()) != ''.join(pair.question_text.split()):
                    raise ValueError(f'原题号 {pair.question_number} 的当前题干与已核对题干不一致')
                pages=[]
                for location in dict.fromkeys(pair.locations):
                    page = self.conn.execute('SELECT text,location_kind FROM pages WHERE source_id=? AND ordinal=?',
                        (answer_source_id,location)).fetchone()
                    if page is None or not page['text'].strip():
                        raise ValueError(f'答案位置 {location} 不存在或没有文字')
                    pages.append(page)
                values.append((q['id'],answer_source_id,'\n'.join(p['text'] for p in pages),
                    encode(list(dict.fromkeys(pair.locations))),pair.basis,encode(pair.missing)))
            self.conn.execute('DELETE FROM answers WHERE source_id=? AND question_id IN '
                '(SELECT id FROM questions WHERE source_id=?)',(answer_source_id,question_source_id))
            self.conn.executemany('INSERT INTO answers(question_id,source_id,text,locations,basis,missing) VALUES(?,?,?,?,?,?)',values)
        return {'question_source_id':question_source_id,'answer_source_id':answer_source_id,'paired':len(values),
            'partial_answers':sum(bool(p.missing) for p in pairs)}

    def augment_word(self, source_id, parsed):
        """Add layout images without changing stems, question IDs or answer links."""
        with self.conn:
            current = {r['number']:dict(r) for r in self.conn.execute(
                'SELECT * FROM questions WHERE source_id=?',(source_id,))}
            for question in parsed.questions:
                if question.number not in current or current[question.number]['text'] != question.text:
                    raise ValueError('题目内容已改变，不能直接补图；请重新核对原题')
            self.conn.execute('DELETE FROM rendered_pages WHERE source_id=?',(source_id,))
            for page in parsed.rendered_pages:
                self.conn.execute('INSERT INTO rendered_pages VALUES(?,?,?,?,?,?)',
                    (source_id,page.ordinal,page.text,page.image,page.location_kind,page.printed_label))
            for question in parsed.questions:
                if question.rendered_text:
                    self.conn.execute('UPDATE questions SET images=?,image_regions=?,rendered_text=?,dedup_layout=? WHERE id=?',
                        (encode(question.images),encode([r.model_dump() for r in question.image_regions]),
                         question.rendered_text,question.dedup_layout,current[question.number]['id']))
            source = self.source(source_id)
            notes = list(dict.fromkeys([*json.loads(source['notes']),*parsed.notes]))
            self.conn.execute('UPDATE sources SET notes=?,processed_at=?,error=NULL WHERE id=?',(encode(notes),time.time(),source_id))

    def _rows(self, course, **filters):
        clauses = ['s.course_id=?', 's.remote_removed=0', "s.status IN ('ready','partial')"]
        params = [self.course_id(course)]
        for key, value in filters.items():
            if key not in {'year', 'exam', 'category', 'question_type'}:
                raise ValueError('未知检索过滤条件')
            if value is not None:
                clauses.append(('q.' if key == 'question_type' else 's.')+key+'=?')
                params.append(value)
        return [self.question_dict(r) for r in self.conn.execute('''SELECT q.*,s.course_id,s.path,s.member,s.url,s.category,s.year,s.exam
            FROM questions q JOIN sources s ON s.id=q.source_id WHERE '''+' AND '.join(clauses), params)]

    def _groups(self,rows):
        """Exact stems only. Case, spacing, formulas and all origins are retained."""
        parents = {q['id']:q['id'] for q in rows}
        def root(qid):
            while parents[qid] != qid:
                parents[qid] = parents[parents[qid]]
                qid = parents[qid]
            return qid
        keys = {}
        by_id={q['id']:q for q in rows}
        rasters={}
        crops={}
        retained_pairs=set()
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_duplicates'").fetchone():
            retained_pairs={tuple(r) for r in self.conn.execute('SELECT first_id,second_id FROM question_duplicates')}
        def same_original(left,right):
            a,b=self.source(left['source_id']).get('local_path'),self.source(right['source_id']).get('local_path')
            return bool(a and b and Path(a).is_file() and Path(b).is_file() and filecmp.cmp(a,b,shallow=False))
        def same_raster(left,right):
            from .layout import raster_content
            for question in (left,right):
                if question['id'] in rasters:
                    continue
                source=self.source(question['source_id'])
                path=Path(source['local_path']) if source['local_path'] else None
                if (source['member'] or source['path']).lower().endswith(('.doc','.docx')):
                    path=self.path.parent/'processed'/str(source['id'])/'document.pdf'
                try:
                    rasters[question['id']]=raster_content(path,question) if path and path.is_file() else []
                except (OSError,ValueError):
                    rasters[question['id']]=[]
            return bool(rasters[left['id']] and rasters[left['id']]==rasters[right['id']])
        def same_crop(left,right):
            # Cleaned PDFs still have complete registered question crops. Exact
            # decoded pixels can establish equality on a later repeated import.
            import pymupdf
            for question in (left,right):
                if question['id'] in crops:
                    continue
                view=dict(question)
                self.attach_previews(view)
                value=[]
                if view['image_previews'] and all(p['kind'] in ('crop','page') for p in view['image_previews']):
                    try:
                        for item in view['images']:
                            path=Path(item)
                            if not path.resolve().is_relative_to((self.path.parent/'processed').resolve()):
                                value=[]
                                break
                            image=pymupdf.Pixmap(str(path))
                            value.append((image.width,image.height,image.n,image.samples))
                    except (OSError,ValueError,RuntimeError):
                        value=[]
                crops[question['id']]=value
            return bool(crops[left['id']] and crops[left['id']]==crops[right['id']])
        for question in rows:
            for text in (question['text'],question.get('rendered_text','')):
                if not text:
                    continue
                # A changed question number alone does not create a new question.
                stem = re.sub(r'^\s*(?:[一二三四五六七八九十]+|(?:第\s*)?\d{1,2})\s*[、．.]\s*','',text,count=1)
                layout=question.get('dedup_layout','')
                # Unknown image/formula layout cannot establish equality from flat
                # text alone. Raster diagrams additionally require identical originals.
                key = (question['course_id'],stem,layout)
                guarded=not layout and bool(question['images']) or bool(layout and json.loads(layout).get('raster'))
                alternatives=keys.setdefault(key,[])
                for previous_id in alternatives:
                    if previous_id==question['id']:
                        break
                    if guarded:
                        previous=by_id[previous_id]
                        pair=tuple(sorted((question['id'],previous['id'])))
                        if (pair not in retained_pairs and not same_original(question,previous)
                                and not (layout and same_raster(question,previous)) and not same_crop(question,previous)):
                            continue
                    a,b = root(question['id']),root(previous_id)
                    parents[max(a,b)] = min(a,b)
                    break
                else:
                    alternatives.append(question['id'])
        groups = {}
        for question in rows:
            groups.setdefault(root(question['id']),[]).append(question)
        return list(groups.values())

    def preserve_duplicates(self, course):
        """Keep proven equality after raw files go away; FK deletion invalidates it."""
        pairs=[pair for group in self._groups(self._rows(course))
               for pair in combinations(sorted(q['id'] for q in group),2)]
        self.conn.executemany('INSERT OR IGNORE INTO question_duplicates VALUES(?,?)',pairs)

    def source_file_kind(self, source_id):
        source=self.source(source_id)
        path=Path(source['local_path']) if source['local_path'] else None
        if path and path.is_file() and path.resolve().is_relative_to((self.path.parent/'raw').resolve()):
            return 'raw'
        if (source['member'] or source['path']).lower().endswith(('.doc','.docx')):
            if (self.path.parent/'processed'/str(source_id)/'document.pdf').is_file():
                return 'converted_pdf'
        return 'remote'

    def _with_origins(self, question, group):
        data = dict(question)
        data.pop('rendered_text',None)
        data.pop('dedup_layout',None)
        data['occurrence_ids'] = [q['id'] for q in group]
        data['origins'] = [{key:q[key] for key in ('id','source_id','number','path','member','url','pages','year','exam')}
                           for q in group]
        for origin in data['origins']:
            origin['file_kind']=self.source_file_kind(origin['source_id'])
        return data

    def rows(self, course, **filters):
        return [self._with_origins(min(group,key=lambda q:q['id']),group)
                for group in self._groups(self._rows(course,**filters))]

    @staticmethod
    def question_dict(row):
        data = dict(row)
        suffix = Path(data.get('member') or data.get('path', '')).suffix.lower()
        data['content_format'] = {'.md': 'markdown', '.html': 'markdown', '.htm': 'markdown', '.txt': 'plain'}.get(suffix, 'image')
        if suffix == '.docx' and not data.get('rendered_text'):
            data['content_format'] = 'plain'
        for key in ('pages', 'subquestions', 'topics', 'images', 'missing'):
            data[key] = json.loads(data[key])
        data['image_regions'] = json.loads(data.get('image_regions','[]'))
        data.pop('normalized', None)
        return data

    def get(self, ids, include_answer=False):
        results = []
        groups = {}
        courses = [r[0] for r in self.conn.execute('SELECT DISTINCT s.course_id FROM questions q JOIN sources s ON s.id=q.source_id '
            'WHERE q.id IN ('+','.join('?' for _ in ids)+')',ids)] if ids else []
        for course in courses:
            for group in self._groups(self._rows(course)):
                for question in group:
                    groups[question['id']] = group
        returned = set()
        for qid in dict.fromkeys(ids):
            group = groups.get(qid)
            if not group or qid in returned:
                continue
            data = self._with_origins(next(q for q in group if q['id']==qid),group)
            returned.update(data['occurrence_ids'])
            word_previews=[q for q in group if q.get('rendered_text') and q['images']
                and (q['member'] or q['path']).lower().endswith(('.doc','.docx'))]
            if word_previews:
                preview=min(word_previews,key=lambda q:(bool(q['member']),q['id']))
                data.update(images=preview['images'],image_regions=preview['image_regions'],
                    preview_source_id=preview['source_id'],preview_number=preview['number'],preview_pages=preview['pages'])
            self.attach_previews(data)
            answers = self.conn.execute('''SELECT a.*,s.url,s.path,s.member FROM answers a
                JOIN sources s ON s.id=a.source_id WHERE a.question_id IN ('''+','.join('?' for _ in group)+''') AND s.remote_removed=0
                AND s.status IN ('ready','partial')''', data['occurrence_ids']).fetchall()
            data['answer_available'] = bool(answers)
            if include_answer:
                data['answers'] = []
                answer_groups = {}
                for a in answers:
                    answer = dict(a)
                    answer['locations'] = json.loads(answer['locations'])
                    answer['missing'] = json.loads(answer.get('missing','[]'))
                    answer['status'] = 'partial' if answer['missing'] else 'source_provided'
                    page = self.conn.execute('SELECT location_kind FROM pages WHERE source_id=? AND ordinal=?',
                        (answer['source_id'],answer['locations'][0])).fetchone()
                    answer['location_kind'] = page[0] if page else 'page'
                    origin={key:answer[key] for key in ('source_id','url','path','member','locations','location_kind')}
                    origin['file_kind']=self.source_file_kind(answer['source_id'])
                    key=(answer['text'],encode(answer['missing']),answer['location_kind'])
                    if key in answer_groups:
                        answer_groups[key]['origins'].append(origin)
                    else:
                        answer['origins']=[origin]
                        answer_groups[key]=answer
                        data['answers'].append(answer)
            results.append(data)
        return results

    def attach_previews(self, data):
        """Join images to this question's current source pages, including older imports."""
        source_id=data.get('preview_source_id',data['source_id'])
        original_ordinals=data.get('preview_pages',data['pages'])
        pages = {r['ordinal']:dict(r) for r in self.conn.execute(
            'SELECT ordinal,image,printed_label,location_kind FROM pages WHERE source_id=?', (source_id,))
            if r['ordinal'] in original_ordinals}
        ordinals = original_ordinals
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rendered_pages'").fetchone():
            rendered = {r['ordinal']:dict(r) for r in self.conn.execute(
                'SELECT ordinal,image,printed_label,location_kind FROM rendered_pages WHERE source_id=?',(source_id,))}
            if rendered and data['image_regions']:
                data['original_locations'] = list(pages.values())
                ordinals = list(dict.fromkeys(r['page'] for r in data['image_regions']))
                pages = rendered
                data['converted_from_word'] = True
        data['source_pages'] = list(pages[p] for p in ordinals if p in pages)
        regions = {r['index']:r for r in data['image_regions']}
        previews = []
        for index,path in enumerate(data['images']):
            region = regions.get(index,{})
            ordinal = region.get('page')
            if ordinal not in pages:
                ordinal = next((p for p,info in pages.items() if info['image']==path),None)
                match = re.search(r'page-(\d+)(?:-|\.)',Path(path).name)
                if ordinal is None and match and int(match[1]) in pages:
                    ordinal = int(match[1])
                if ordinal is None and len(pages)==1:
                    ordinal = next(iter(pages))
            page = pages.get(ordinal,{})
            kind = ('embedded' if page.get('location_kind')=='paragraph' else
                'page' if page.get('image')==path else 'crop' if page.get('location_kind')=='page' else 'image')
            previews.append({'index':index,'page':ordinal,'printed_label':region.get('printed_label') or page.get('printed_label'),
                'location_kind':page.get('location_kind'),'kind':kind,'bbox':region.get('bbox')})
        data['image_previews'] = previews

    def coverage(self, course):
        cid = self.course_id(course)
        sources = self.sources(cid)
        states = {}
        for s in sources:
            states[s['status']] = states.get(s['status'], 0) + 1
        row = self.conn.execute('SELECT checked_at,discovery_error FROM courses WHERE id=?', (cid,)).fetchone()
        return {'discovered_files': sum(not s['member'] for s in sources), 'archive_members': sum(bool(s['member']) for s in sources),
            'downloaded': sum(bool(s['local_path'] or s.get('raw_deleted_at')) for s in sources), 'states': states,
            'raw_retained': sum(bool(s['local_path']) and Path(s['local_path']).is_file() for s in sources),
            'raw_deleted': sum(bool(s.get('raw_deleted_at')) for s in sources),
            'remote_removed': sum(s['remote_removed'] for s in sources),
            'questions': len(self.rows(cid)), 'question_occurrences':len(self._rows(cid)),
            'years': sorted({q['year'] for q in self._rows(cid) if q['year'] is not None}),
            'last_checked': row['checked_at'] if row else None, 'discovery_error': row['discovery_error'] if row else None,
            'scope': '仅本仓库已发现且成功处理的资料，不代表课程全部历年试题'}
