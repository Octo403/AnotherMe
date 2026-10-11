import io
import json
from pathlib import Path
import zipfile

import pytest

from aitown.question_bank.archives import expand_archive, seven_zip_members, validate_members
from aitown.question_bank.models import BankConfig, COURSE_ID, ImageRegion, Page, ParsedSource, Question, Source
from aitown.question_bank.prepare import parse_pdf
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank
from aitown.question_bank.models import GetArgs, SearchArgs
from aitown.question_bank.word import attach_word_layout


@pytest.fixture
def local_bank(tmp_path):
    config=BankConfig(root=tmp_path/'bank')
    with BankRepository(config.database) as repo:
        repo.save_discovery([{'id':COURSE_ID,'name':'集合与图论','path':'public/course'}],[])
        yield config,repo


def add_source(repo,path,questions,*,year=2023,rendered_pages=None):
    with repo.conn:
        sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/repo',path=path,url='https://example.org/'+path))
    repo.replace_parsed(sid,ParsedSource(pages=[Page(ordinal=239,text='original paragraph',location_kind='paragraph')],
        rendered_pages=rendered_pages or [],questions=questions,year=year,exam='final',category='exam'))
    return sid


def test_word_augmentation_preserves_ids_paragraph_answers_and_pdf_pages(local_bank):
    config,repo=local_bank
    text='七、证明第一类Stirling数满足递推公式。'
    q=Question(number='七',text=text,pages=[239])
    sid=add_source(repo,'exam.docx',[q])
    qid=repo.rows(COURSE_ID)[0]['id']
    with repo.conn:
        repo.conn.execute('INSERT INTO answers(question_id,source_id,text,locations,basis) VALUES(?,?,?,?,?)',
            (qid,sid,'answer sentinel','[239]','reviewed'))
    rendered_q=Question(number='七',text=text,pages=[6],images=['crop.png'],
        image_regions=[ImageRegion(index=0,page=6,bbox=[20,30,500,100])])
    parsed=attach_word_layout(ParsedSource(pages=[],questions=[q]),
        ParsedSource(pages=[Page(ordinal=6,text=text,image='page.png')],questions=[rendered_q]))
    repo.augment_word(sid,parsed)
    got=repo.get([qid],True)[0]
    assert got['id']==qid and got['pages']==[239]
    assert got['source_pages'][0]['ordinal']==6 and got['converted_from_word']
    assert got['image_previews'][0]['kind']=='crop'
    assert got['answers'][0]['locations']==[239] and got['answers'][0]['text']=='answer sentinel'
    assert 'answers' not in repo.get([qid])[0]
    before=repo.conn.execute('SELECT images FROM questions WHERE id=?',(qid,)).fetchone()[0]
    parsed.questions[0].text='changed content'
    with pytest.raises(ValueError):repo.augment_word(sid,parsed)
    assert repo.conn.execute('SELECT images FROM questions WHERE id=?',(qid,)).fetchone()[0]==before


def test_heading_alone_does_not_attach_wrong_question():
    old=ParsedSource(pages=[],questions=[Question(number='一',text='一、证明树的所有顶点满足所给条件。',pages=[1])])
    rendered=ParsedSource(pages=[],questions=[Question(number='一',text='一、证明平面图存在一个至少五度结点。',pages=[1],images=['wrong.png'])])
    result=attach_word_layout(old,rendered)
    assert not result.questions[0].images and not result.questions[0].rendered_text


def test_duplicate_word_zip_pdf_one_result_all_origins_answers_and_exclusion(local_bank):
    config,repo=local_bank
    native='一、证明 |P(A)|=2^(|A|)。'
    exported='一、证明 |P(A)|=2|A|。'
    sid=add_source(repo,'exam.docx',[Question(number='一',text=native,rendered_text=exported,pages=[239])])
    duplicate=add_source(repo,'zip/exam.docx',[Question(number='一',text=native,rendered_text=exported,pages=[239])])
    pdf=add_source(repo,'exam.pdf',[Question(number='一',text=exported,pages=[1])])
    raw_ids=[r[0] for r in repo.conn.execute('SELECT id FROM questions ORDER BY id')]
    with repo.conn:
        repo.conn.execute('INSERT INTO answers(question_id,source_id,text,locations,basis) VALUES(?,?,?,?,?)',
            (raw_ids[1],duplicate,'answer only on duplicate','[239]','reviewed'))
    rows=repo.rows(COURSE_ID)
    assert len(rows)==1 and len(rows[0]['origins'])==3
    assert 'rendered_text' not in rows[0]
    bank=QuestionBank(config)
    result=bank.get(GetArgs(question_ids=raw_ids,include_answer=True))
    assert result.status=='ok' and result.count==1 and not result.missing_ids
    assert result.results[0]['answers'][0]['text']=='answer only on duplicate'
    assert bank.search(SearchArgs(query='证明题',exclude_ids=[raw_ids[2]])).count==0
    assert repo.get([raw_ids[2]])[0]['id']==raw_ids[2] # Existing citations retain their own ID.
    with repo.conn:repo.conn.execute('UPDATE sources SET remote_removed=1 WHERE id=?',(sid,))
    assert len(repo.rows(COURSE_ID))==1 and repo.rows(COURSE_ID)[0]['id']==raw_ids[1]
    assert not repo.get([raw_ids[0]])


def test_no_fuzzy_merge_math_case_spacing_conditions_or_other_courses(local_bank):
    _,repo=local_bank
    texts=['一、证明 A=a。','一、证明 a=a。','一、证明 ab=1。','一、证明 a b=1。','一、证明 ab=2。']
    add_source(repo,'variants.docx',[Question(number=str(i),text=t,pages=[239]) for i,t in enumerate(texts)])
    add_source(repo,'repeat.docx',[Question(number='八',text='八、证明 A=a。',pages=[239])],year=2024)
    assert len(repo.rows(COURSE_ID))==5
    assert len(repo.rows(COURSE_ID,year=2024))==1


def test_flattened_math_does_not_merge_different_script_placement(local_bank):
    _,repo=local_bank
    for number,styles in [('1',[[[1,0],[.7,-.3]]]),('2',[[[1,0],[1,0]]])]:
        add_source(repo,number+'.pdf',[Question(number=number,text=number+'. 证明 A2=B。',pages=[1],
            dedup_layout=json.dumps({'styles':styles,'raster':False}))])
    assert len(repo.rows(COURSE_ID))==2


def test_duplicate_pdf_uses_completed_word_crop_without_changing_citation_id(local_bank):
    _,repo=local_bank
    text='七、证明原题满足给定的数学条件。'
    pdf=add_source(repo,'exam.pdf',[Question(number='七',text=text,pages=[6],images=['old-page.png'])])
    # An older PDF lacks layout metadata; no images here until equivalence is established.
    with repo.conn:repo.conn.execute("UPDATE questions SET dedup_layout=? WHERE source_id=?",(json.dumps({'raster':False}),pdf))
    word=add_source(repo,'exam.docx',[Question(number='七',text=text,rendered_text=text,pages=[239],
        images=['tight-crop.png'],image_regions=[ImageRegion(index=0,page=6)],dedup_layout=json.dumps({'raster':False}))],
        rendered_pages=[Page(ordinal=6,text=text,image='word-page.png')])
    qid=repo.rows(COURSE_ID)[0]['id']
    got=repo.get([qid])[0]
    assert got['id']==qid and got['source_id']==pdf and got['pages']==[6]
    assert got['images']==['tight-crop.png'] and got['preview_source_id']==word
    assert got['source_pages'][0]['image']=='word-page.png' and got['converted_from_word']


def test_math_layout_records_superscript_and_raster_equality(tmp_path):
    import pymupdf as fitz
    from aitown.question_bank.layout import layout_traits,raster_content
    values=[]
    for index in range(2):
        with fitz.open() as pdf:
            page=pdf.new_page(width=200,height=200)
            page.insert_text((20,40),'A')
            page.insert_text((30,36 if index==0 else 40),'2',fontsize=8 if index==0 else 11)
            text=page.get_text(sort=True).strip()
            q=Question(number='1',text=text,pages=[1])
            values.append(layout_traits(pdf,q))
    assert values[0]!=values[1]


def test_same_text_with_different_vector_diagrams_is_distinct(tmp_path):
    import pymupdf as fitz
    from aitown.question_bank.layout import layout_traits
    values=[]
    for width in (40,80):
        with fitz.open() as pdf:
            page=pdf.new_page(width=200,height=200)
            page.insert_text((20,40),'1. Prove the graph condition')
            page.draw_rect(fitz.Rect(20,70,20+width,120))
            q=Question(number='1',text=page.get_text(sort=True).strip(),pages=[1],
                image_regions=[ImageRegion(index=0,page=1,bbox=[0,20,200,160])])
            values.append(layout_traits(pdf,q))
    assert values[0]!=values[1]


def test_word_mixed_answer_notes_are_withheld(local_bank,tmp_path,monkeypatch):
    from aitown.question_bank import word
    from aitown.question_bank import prepare
    original=ParsedSource(pages=[Page(ordinal=1,text='证明思想：答案在此')],questions=[Question(number='1',text='原题',pages=[1])])
    rendered=ParsedSource(pages=[Page(ordinal=1,text='证明思想：答案在此')],questions=[Question(number='1',text='原题',pages=[1],images=['unsafe.png'])])
    monkeypatch.setattr(prepare,'parse_docx',lambda *args:original)
    monkeypatch.setattr(prepare,'parse_pdf',lambda *args,**kwargs:rendered)
    monkeypatch.setattr(word,'export_word',lambda *args:tmp_path/'fake.pdf')
    parsed=word.parse_word(tmp_path/'mixed.docx',{'category_hint':'practice'},tmp_path)
    assert parsed.status=='needs_review' and not parsed.questions[0].images
    config,repo=local_bank
    sid=add_source(repo,'mixed.docx',[])
    repo.replace_parsed(sid,parsed)
    assert not repo.rows(COURSE_ID) and repo.source(sid)['status']=='needs_review'


def test_tight_pdf_crops_continuation_diagram_and_excludes_solution(tmp_path):
    import pymupdf as fitz
    path=tmp_path/'word.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=400,height=500)
        page.insert_text((30,40),'1. Prove this condition')
        page.draw_rect(fitz.Rect(40,80,140,140)) # Diagram below text must survive.
        page.insert_text((30,390),'第 1 页',fontname='china-s')
        page.insert_text((15,300),'装订线内不要答题',fontname='china-s',rotate=90)
        page=pdf.new_page(width=400,height=500)
        page.insert_text((30,40),'Continuation of first question')
        page.insert_text((30,110),'Solution: secret answer')
        page.insert_text((30,180),'2. Prove another condition')
        pdf.save(path)
    # Use the parser's Chinese answer boundary in an available embedded CJK font.
    with fitz.open(path) as pdf:
        pdf[1].insert_text((30,95),'解：',fontname='china-s')
        pdf.save(tmp_path/'with-answer.pdf')
    parsed=parse_pdf(tmp_path/'with-answer.pdf',{'category_hint':'exam'},tmp_path/'out',tight=True)
    q=parsed.questions[0]
    assert q.pages==[1,2] and len(q.images)==2
    assert 'secret answer' not in q.text
    assert q.image_regions[0].bbox[3]>=140 and q.image_regions[0].bbox[3]<170
    assert q.image_regions[1].bbox[3]<95


def test_matrix_bottom_is_not_cut_at_spine_text(tmp_path):
    import pymupdf as fitz
    path=tmp_path/'matrix.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=400,height=500)
        page.insert_text((70,40),'1. Calculate the determinant')
        for y in (70,100,130,160):
            page.insert_text((100,y),'1   0   1',fontsize=18)
        page.draw_line((95,50),(95,170))
        page.draw_line((190,50),(190,170))
        # This margin starts after the last glyph's top but before its bottom.
        page.insert_text((15,166),'题',fontname='china-s',fontsize=8)
        pdf.save(path)
    parsed=parse_pdf(path,{'category_hint':'exam'},tmp_path/'out',tight=True)
    assert len(parsed.questions)==1
    assert parsed.questions[0].image_regions[0].bbox[3]>=170
    assert '题' not in parsed.questions[0].text


def test_formula_fragments_sharing_heading_baseline_belong_to_new_question(tmp_path):
    import pymupdf as fitz
    path=tmp_path/'headings.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=500,height=500)
        page.insert_text((40,40),'一、证明给定条件。',fontname='china-s')
        page=pdf.new_page(width=500,height=500)
        # An embedded equation starts higher; its block is emitted before the
        # Chinese heading despite belonging to the same question line.
        page.insert_text((260,80),'matrix continuation')
        page.insert_text((220,65),'A',fontsize=12)
        page.insert_text((40,80),'二、证明矩阵满足条件。',fontname='china-s')
        page.insert_text((220,95),'B',fontsize=12)
        pdf.save(path)
    parsed=parse_pdf(path,{'category_hint':'exam'},tmp_path/'out',tight=True)
    assert len(parsed.questions)==2
    assert parsed.questions[0].pages==[1]
    assert parsed.questions[1].pages==[2]
    assert 'matrix continuation' in parsed.questions[1].text
    assert 'matrix continuation' not in parsed.questions[0].text
    assert parsed.questions[1].image_regions[0].bbox[1]<65


@pytest.mark.parametrize('name,extra',[
    ('../escape.docx',{}),('C:/escape.docx',{}),('safe/link.docx',{'link':True}),
    ('safe/password.docx',{'encrypted':True}),('AUX.docx',{}),('file.docx:stream',{}),
])
def test_archive_validation_rejects_before_writing(tmp_path,name,extra):
    members=[{'name':'safe.docx','size':1},{'name':name,'size':1,**extra}]
    with pytest.raises(ValueError):validate_members(members,tmp_path/'out',BankConfig())
    assert not (tmp_path/'out').exists()


def test_archive_case_collision_and_slt_unicode(tmp_path):
    with pytest.raises(ValueError,match='冲突'):
        validate_members([{'name':'Exam.docx','size':1},{'name':'exam.docx','size':1}],tmp_path,BankConfig())
    records=seven_zip_members('Path = 中文试卷.docx\nFolder = -\nSize = 5\nAttributes = A\n\nPath = bad\nSize = 0\nSymbolic Link = ../bad\n')
    assert records[0]['name']=='中文试卷.docx' and records[1]['link']


def test_zip_actually_extracts_nested_member_and_retains_source(local_bank,tmp_path):
    config,repo=local_bank
    sid=add_source(repo,'archive.zip',[])
    path=tmp_path/'archive.zip'
    content='1. 证明集合满足条件。'.encode()
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('真题/试卷.txt',content)
    child=expand_archive(repo,repo.source(sid),path,config)[0]
    source=repo.source(child)
    assert Path(source['local_path']).read_bytes()==content
    assert source['member']=='真题/试卷.txt' and source['url']==repo.source(sid)['url']


def test_original_member_endpoint_is_current_and_confined_to_raw(local_bank,tmp_path):
    from types import SimpleNamespace
    from aitown.ui.service import UIService,UIError
    config,repo=local_bank
    sid=add_source(repo,'archive.zip',[Question(number='一',text='一、证明原题满足给定的数学条件。',pages=[239])])
    qid=repo.rows(COURSE_ID)[0]['id']
    target=config.root/'raw'/'members'/'原卷.docx'
    target.parent.mkdir(parents=True);target.write_bytes(b'original member')
    repo.set_source(sid,local_path=str(target))
    service=object.__new__(UIService)
    service.settings=SimpleNamespace(question_bank_dir=config.root)
    service.agent=SimpleNamespace(question_bank=QuestionBank(config))
    assert service._question_source(qid,sid)==(b'original member',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document','原卷.docx')
    with pytest.raises(UIError):service._question_source(qid,sid+1)
    outside=tmp_path/'personal.docx';outside.write_bytes(b'outside')
    repo.set_source(sid,local_path=str(outside))
    with pytest.raises(UIError):service._question_source(qid,sid)
    repo.set_source(sid,local_path=str(target),status='failed')
    with pytest.raises(UIError):service._question_source(qid,sid)
