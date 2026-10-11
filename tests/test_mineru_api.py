import io
import json
import zipfile
from pathlib import Path

import httpx
import pymupdf as fitz
import pytest

from aitown.question_bank.mineru_api import normalize_bundle, parse_mineru_api_pdf, public_url, validate_source
from aitown.question_bank.models import BankConfig, COURSE_ID, Source


def original_source():
    return Source(course_id=COURSE_ID,repository='victkk/FDU-Sharing',
        path='public/resources/集合与图论/test.pdf',
        url='https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/集合与图论/test.pdf',
        category_hint='exam',year_hint=2025).model_dump()


def archive_bytes(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,data in files.items():archive.writestr(name,json.dumps(data,ensure_ascii=False))
    return buffer.getvalue()


@pytest.mark.parametrize('kind',['default','other-course','zip-member','nested-zip-member'])
def test_public_original_upload_poll_parse_images_and_token_isolation(tmp_path,monkeypatch,kind):
    from aitown.question_bank.sources import GithubSource
    document=fitz.open();page=document.new_page();page.insert_text((50,100),'1. Prove the relation is transitive.')
    pristine=document.tobytes();document.close()
    source=original_source()
    public_bytes=pristine
    if kind!='default':
        source.update(course_id='线性代数',path='public/resources/线性代数/test.pdf',
            url='https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/线性代数/test.pdf')
    if kind.endswith('zip-member'):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('原卷/exam.pdf',pristine)
        public_bytes=buffer.getvalue()
        source.update(path='public/resources/线性代数/exams.zip',member='原卷/exam.pdf',size=len(pristine),
            url='https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/线性代数/exams.zip')
        if kind=='nested-zip-member':
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('inner.zip',public_bytes)
            public_bytes=buffer.getvalue();source['member']='inner.zip!/原卷/exam.pdf'
    local=tmp_path/'annotated.pdf';local.write_bytes(b'PRIVATE_ANNOTATIONS_MUST_NOT_UPLOAD')
    downloads=[]
    def download(self,source,destination,**kwargs):
        assert kwargs['update']
        if kind.endswith('zip-member'):assert not source['member'] and source['size'] is None
        downloads.append(source['url']);destination.write_bytes(public_bytes);return destination
    monkeypatch.setattr(GithubSource,'download',download)
    result_zip=archive_bytes({'course_content_list.json':[
        {'type':'text','text':'1. 证明等价关系具有传递性。','page_idx':0,'bbox':[50,100,950,200]}]})
    requests=[]
    def handle(request):
        requests.append(request)
        if request.url.host=='mineru.net':
            assert request.headers['Authorization']=='Bearer synthetic-mineru-token'
            if request.method=='POST':
                payload=json.loads(request.content)
                assert payload['model_version']=='vlm' and payload['enable_formula']
                return httpx.Response(200,json={'code':0,'data':{'batch_id':'test-batch','file_urls':['https://storage.example/upload']}})
            return httpx.Response(200,json={'code':0,'data':{'extract_result':[{
                'state':'done','file_name':'course.pdf','full_zip_url':'https://storage.example/result.zip'}]}})
        assert 'Authorization' not in request.headers
        if request.method=='PUT':
            assert request.read()==pristine
            assert b'PRIVATE_ANNOTATIONS' not in request.content
            return httpx.Response(200)
        return httpx.Response(200,content=result_zip)
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    config=BankConfig(root=tmp_path/'bank',extractor='mineru_api',mineru_api_key='synthetic-mineru-token')
    parsed=parse_mineru_api_pdf(local,source,tmp_path/'parsed',config)
    assert downloads and len(requests)==4
    assert parsed.status=='partial' and len(parsed.pages)==1 and len(parsed.questions)==1
    question=parsed.questions[0]
    assert question.question_type=='证明' and question.images and Path(question.images[0]).is_file()
    assert question.image_regions[0].page==1
    assert 'synthetic-mineru-token' not in parsed.model_dump_json()


@pytest.mark.parametrize('changes',[
    {'repository':'private/repo'}, {'path':'personal.pdf'}, {'member':'inside.pdf'},
    {'url':'https://private.example/private.pdf'}, {'path':'public/resources/集合与图论/../private.pdf'}])
def test_files_outside_authorized_public_course_rejected_before_network(changes,tmp_path,monkeypatch):
    source={**original_source(),**changes}
    def forbidden(*args,**kwargs):raise AssertionError('must reject before network')
    monkeypatch.setattr(httpx,'Client',forbidden)
    with pytest.raises(ValueError,match='授权范围'):
        parse_mineru_api_pdf(None,source,tmp_path/'parsed',BankConfig(mineru_api_key='token'))


def test_legacy_layout_coordinates_and_resource_bounds():
    raw=archive_bytes({'layout.json':{'pdf_info':[{'page_idx':0,'page_size':[600,800],
        'preproc_blocks':[{'type':'text','bbox':[60,80,540,160],
            'lines':[{'spans':[{'content':'1. Prove A.'}]}]}]}]}})
    normalized=normalize_bundle(raw,BankConfig())
    assert normalized['pages'][0]['blocks'][0]['bbox']==[.1,.1,.9,.2]
    oversized=archive_bytes({'a.json':{},'b.json':{}})
    with pytest.raises(ValueError,match='限制'):
        normalize_bundle(oversized,BankConfig(max_archive_members=1))
    with pytest.raises(ValueError,match='缺少'):
        normalize_bundle(archive_bytes({'../../outside.json':{}}),BankConfig())


@pytest.mark.parametrize('url',['http://storage.example/file','https://127.0.0.1/file',
    'https://[::1]/file','https://user:key@storage.example/file','file:///private'])
def test_signed_storage_urls_must_be_public_https(url):
    with pytest.raises(ValueError):public_url(url)
