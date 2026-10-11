import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
from functools import lru_cache

import pytest
import pymupdf

from aitown.question_bank.cleanup import cleanup_raw
from aitown.question_bank.models import BankConfig, COURSE_ID, Page, ParsedSource, Question, Source
from aitown.question_bank.repository import BankRepository


@pytest.fixture
def bank(tmp_path):
    config=BankConfig(root=tmp_path/'bank')
    with BankRepository(config.database) as repo:
        repo.save_discovery([{'id':COURSE_ID,'name':'集合与图论','path':'course'}],[])
        yield config,repo


@lru_cache
def artifacts(content=b'original'):
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=100,height=100)
        page.insert_text((10,20),content.decode('ascii'))
        return pdf.tobytes(),page.get_pixmap().tobytes('png')


def add(repo,config,name,*,content=b'original',status='partial',images=True):
    with repo.conn:
        sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path=name,
            url='https://example.org/'+name))
    raw=config.root/'raw'/str(sid)/name
    raw.parent.mkdir(parents=True);raw.write_bytes(artifacts(content)[0] if name.endswith('.pdf') else content)
    output=config.root/'processed'/str(sid);output.mkdir(parents=True)
    image=output/'page-1.png';image.write_bytes(artifacts(content)[1])
    q=Question(number='一',text='一、证明给定图具有所要求的性质。',pages=[1],images=[str(image)] if images else [])
    pages=[Page(ordinal=1,text=q.text,image=str(image))]
    repo.replace_parsed(sid,ParsedSource(pages=pages,questions=[q],status='partial'))
    repo.set_source(sid,local_path=str(raw),status=status)
    return sid,raw,image


def test_cleanup_preserves_image_guarded_duplicates_origins_and_answers(bank):
    config,repo=bank
    first,raw,image=add(repo,config,'one.pdf')
    second,copy,_=add(repo,config,'two.pdf')
    third,different,_=add(repo,config,'different.pdf',content=b'different diagram')
    before=repo.rows(COURSE_ID)
    assert len(before)==2
    qid=next(q['id'] for q in before if q['source_id']==first)
    with repo.conn:
        repo.conn.execute('INSERT INTO answers(question_id,source_id,text,locations,basis) VALUES(?,?,?,?,?)',
            (qid,second,'source answer','[1]','checked'))
    result=cleanup_raw(config,COURSE_ID)
    assert len(result['deleted'])==3 and not result['failed']
    assert not any(p.exists() for p in (raw,copy,different)) and image.is_file()
    after=repo.rows(COURSE_ID)
    assert [q['occurrence_ids'] for q in after]==[q['occurrence_ids'] for q in before]
    question=repo.get([qid],True)[0]
    assert len(question['origins'])==2 and question['answers'][0]['text']=='source answer'
    assert all(o['file_kind']=='remote' for o in question['origins'])
    assert repo.coverage(COURSE_ID)['raw_deleted']==3
    assert not cleanup_raw(config,COURSE_ID)['deleted']
    # Replacing one source invalidates only that source's proven duplicate links.
    repo.replace_parsed(second,ParsedSource(pages=[],questions=[Question(number='一',text=question['text'],pages=[1],images=['new.png'])]))
    assert len(repo.rows(COURSE_ID))==3


def test_cleanup_retains_unprocessed_missing_images_and_paths_outside_raw(bank,tmp_path):
    config,repo=bank
    kept=[]
    for status in ('failed','needs_review','unsupported','processing'):
        sid,path,_=add(repo,config,status+'.pdf',status=status)
        kept.append(path)
    _,missing,_=add(repo,config,'missing.pdf',images=False);kept.append(missing)
    sid,_,_=add(repo,config,'outside.pdf')
    outside=tmp_path/'outside.pdf';outside.write_bytes(b'personal source')
    repo.set_source(sid,local_path=str(outside));kept.append(outside)
    report=cleanup_raw(config,COURSE_ID)
    assert len(report['retained'])==6 and not report['deleted']
    assert all(path.is_file() for path in kept)


def test_cleanup_reports_failed_delete_and_can_recover_missing_file_metadata(bank,monkeypatch):
    config,repo=bank
    sid,raw,_=add(repo,config,'locked.pdf')
    unlink=Path.unlink
    def locked(path,*args,**kwargs):
        if path==raw:raise PermissionError('synthetic locked file')
        return unlink(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',locked)
        report=cleanup_raw(config,COURSE_ID)
    assert len(report['failed'])==1 and raw.is_file() and repo.source(sid)['local_path']
    raw.unlink() # Simulate stop after unlink but before metadata update.
    assert len(cleanup_raw(config,COURSE_ID)['deleted'])==1
    assert repo.source(sid)['local_path'] is None and repo.source(sid)['raw_deleted_at']


def test_cleanup_refuses_active_course_preparation(bank):
    from aitown.question_bank.jobs import create_job
    config,repo=bank
    _,raw,_=add(repo,config,'exam.pdf')
    create_job(repo,COURSE_ID,update=True)
    with pytest.raises(ValueError,match='正在运行'):cleanup_raw(config,COURSE_ID)
    assert raw.is_file()


def test_cleaned_word_source_serves_converted_pdf(bank):
    from aitown.question_bank.search import QuestionBank
    from aitown.ui.service import UIService,UIError
    config,repo=bank
    sid,raw,image=add(repo,config,'exam.docx')
    document=config.root/'processed'/str(sid)/'document.pdf';document.write_bytes(artifacts()[0])
    with repo.conn:
        repo.conn.execute('INSERT INTO rendered_pages VALUES(?,?,?,?,?,?)',(sid,1,'original page',str(image),'page',None))
    qid=repo.rows(COURSE_ID)[0]['id']
    cleanup_raw(config,COURSE_ID)
    assert not raw.exists() and repo.get([qid])[0]['origins'][0]['file_kind']=='converted_pdf'
    service=object.__new__(UIService)
    service.settings=SimpleNamespace(question_bank_dir=config.root)
    service.agent=SimpleNamespace(question_bank=QuestionBank(config))
    assert service._question_source(qid,sid)==(document.read_bytes(),'application/pdf','document.pdf')
    with pytest.raises(UIError):service._question_source(qid,sid+1)


def test_repeated_pdf_import_after_cleanup_matches_exact_retained_pixels(bank):
    config,repo=bank
    sid,_,_=add(repo,config,'first.pdf')
    cleanup_raw(config,COURSE_ID)
    add(repo,config,'new-copy.pdf')
    rows=repo.rows(COURSE_ID)
    assert len(rows)==1 and len(rows[0]['origins'])==2
    # Equal text with different retained pixels is a separate question.
    add(repo,config,'new-diagram.pdf',content=b'different figure')
    assert len(repo.rows(COURSE_ID))==2


def test_word_completion_restores_cleaned_original_preserves_ids_and_retains_failed_retry(bank,monkeypatch):
    from aitown.question_bank import completion,sources
    config,repo=bank
    sid,raw,image=add(repo,config,'exam.docx')
    document=config.root/'processed'/str(sid)/'document.pdf';document.write_bytes(artifacts()[0])
    with repo.conn:
        repo.conn.execute('INSERT INTO rendered_pages VALUES(?,?,?,?,?,?)',(sid,1,'original page',str(image),'page',None))
    question=repo.rows(COURSE_ID)[0]
    cleanup_raw(config,COURSE_ID)
    class Remote:
        def __init__(self,*args):pass
        def download(self,source,path,**kwargs):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'restored Word')
        def close(self):pass
    monkeypatch.setattr(sources,'GithubSource',Remote)
    def parsed(*args):
        return ParsedSource(pages=[],rendered_pages=[Page(ordinal=1,text=question['text'],image=str(image))],
            questions=[Question(number='一',text=question['text'],rendered_text=question['text'],pages=[1],images=[str(image)])])
    monkeypatch.setattr(completion,'parse_file',parsed)
    first=completion.complete_previews(config,COURSE_ID,source_id=sid)
    assert not first['failed'] and raw.is_file() and repo.rows(COURSE_ID)[0]['id']==question['id']
    cleanup_raw(config,COURSE_ID)
    def fail(*args):raise TimeoutError('synthetic Word export failure')
    monkeypatch.setattr(completion,'parse_file',fail)
    failed=completion.complete_previews(config,COURSE_ID,source_id=sid)
    assert len(failed['failed'])==1 and raw.is_file() and repo.source(sid)['error']
    assert repo.get([question['id']])[0]['images']==[str(image)]
    monkeypatch.setattr(completion,'parse_file',parsed)
    retried=completion.complete_previews(config,COURSE_ID,source_id=sid)
    assert not retried['failed'] and raw.is_file() and not repo.source(sid)['error']


def test_worker_download_extract_parse_cleanup_and_restore_member(bank,monkeypatch):
    from aitown.question_bank import jobs
    config,repo=bank
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('one.txt','1. 证明等价关系的条件甲。')
        archive.writestr('two.txt','2. 证明等价关系的条件乙。')
    with repo.conn:
        parent=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path='exam.zip',url='https://example.org/exam.zip'))
    downloads=[]
    class Remote:
        def __init__(self,*args):pass
        def download(self,source,path,**kwargs):
            downloads.append(source['id'])
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data.getvalue())
        def close(self):pass
    monkeypatch.setattr(jobs,'GithubSource',Remote)
    job=jobs.create_job(repo,COURSE_ID)
    first=jobs.run_job(config,job,progress=lambda _:None)
    assert first['failed']==0 and not first['cleanup']['deleted'] and len(first['cleanup']['retained'])==3
    assert len(cleanup_raw(config,COURSE_ID)['deleted'])==3
    members=[s for s in repo.sources(COURSE_ID) if s['member']]
    assert all(s['local_path'] is None for s in members)
    sibling_id=repo.conn.execute('SELECT id FROM questions WHERE source_id=?',(members[1]['id'],)).fetchone()[0]
    job=jobs.create_job(repo,COURSE_ID,source_id=members[0]['id'])
    retry=jobs.run_job(config,job,progress=lambda _:None)
    assert retry['failed']==0 and downloads==[parent,parent] and not retry['cleanup']['deleted']
    assert repo.source(members[1]['id'])['status']=='ready'
    assert repo.conn.execute('SELECT id FROM questions WHERE source_id=?',(members[1]['id'],)).fetchone()[0]==sibling_id
    assert repo.coverage(COURSE_ID)['questions']==2 and repo.coverage(COURSE_ID)['raw_retained']==3
