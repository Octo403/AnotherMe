"""Exercise the actual ZIP via Start.bat, using an isolated, relocated copy."""
import argparse
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--api-base', default='http://127.0.0.1:8765/v1')
    parser.add_argument('--api-model', default='qwen3-4b-local')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    from dotenv import dotenv_values
    # The existing local project service is the only inference endpoint used.
    key = dotenv_values('.env').get('LLM_API_KEY', 'local-only')
    run = (Path('data/runtime/portable-verification')/time.strftime('%Y%m%d-%H%M%S')).resolve()
    run.mkdir(parents=True)
    report_path = Path('data/evaluation/question_bank')/('portable-'+run.name+'.json')
    checks = {};report = {'archive':str(args.archive.resolve()), 'run':str(run), 'checks':checks,
        'model':args.api_model, 'inference':'real local API, no Codex CLI; no cloud requests',
        'note':'真实 ZIP 解压后的 Windows 本机验证；不是干净虚拟机或其他 Windows 版本验证。'}
    started = time.monotonic();process = log = client = None
    try:
        with zipfile.ZipFile(args.archive) as archive:
            names = archive.namelist()
            top = names[0].split('/')[0]+'/'
            assert not any(name.startswith((top+'user-data/',top+'data/')) or name==top+'.env' for name in names)
            assert not any(name.startswith((top+'runtime/Lib/site-packages/torch/',top+'runtime/Lib/site-packages/transformers/')) for name in names)
            checks['archive_contains_no_personal_data_keys_or_model_weights'] = True
            archive.extractall(run/'中文 空格 解压目录')
        home = next(p for p in (run/'中文 空格 解压目录').iterdir() if p.is_dir())
        report['home'] = str(home)
        env = dict(os.environ)
        env['PATH'] = os.environ['SystemRoot']+'\\System32;'+os.environ['SystemRoot']
        env['PYTHONPATH'] = 'invalid-developer-python-path'
        env['QUESTION_BANK_DIR'] = 'invalid-developer-bank'
        def launch():
            nonlocal process, log, client
            log = (run/'desktop.log').open('ab')
            process = subprocess.Popen(['cmd.exe','/d','/c','Start.bat','--no-browser','--port','0'],
                cwd=home,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic()+60
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('Start.bat exited; see '+str(run/'desktop.log'))
                try:
                    record = json.loads((home/'user-data/desktop.json').read_text())
                    url = f'http://127.0.0.1:{record["port"]}'
                    candidate = httpx.Client(base_url=url,trust_env=False,timeout=90)
                    response = candidate.get('/api/bootstrap')
                    response.raise_for_status();client=candidate
                    report['url'] = url
                    return response.json()
                except (OSError,ValueError,httpx.HTTPError):
                    time.sleep(.2)
            raise TimeoutError('portable startup')
        def get(path,**kwargs):
            response = client.get(path,**kwargs);response.raise_for_status();return response.json()
        def post(path,payload):
            response = client.post(path,json=payload);response.raise_for_status();return response.json()
        def job(path,payload):
            identifier = post(path,payload)['job_id'];deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                value=get('/api/job',params={'id':identifier})
                if value['status']=='complete':return value['result']
                if value['status']=='failed':raise RuntimeError(value['error'])
                time.sleep(.2)
            raise TimeoutError(path)
        def stop():
            nonlocal process,log,client
            if process and process.poll() is None:
                result = subprocess.run(['cmd.exe','/d','/c','Stop.bat'],cwd=home,env=env,
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                assert result.returncode==0
                process.wait(timeout=30)
            if client:client.close();client=None
            if log:log.close();log=None
            process=None
        boot=launch()
        checks['start_bat_works_without_system_python_in_chinese_space_path'] = boot['setup_required'] and Path(boot['question_bank_directory']).is_relative_to(home/'user-data')
        details=get('/api/model-settings')
        checks['bundled_converter_is_detected'] = details['documents']['libreoffice']
        original_pid=json.loads((home/'user-data/desktop.json').read_text())['pid']
        duplicate=subprocess.run(['cmd.exe','/d','/c','Start.bat','--no-browser'],cwd=home,env=env,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        checks['double_click_reuses_same_service'] = duplicate.returncode==0 and json.loads((home/'user-data/desktop.json').read_text())['pid']==original_pid
        if args.browser:
            edge = Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe'
            browser = subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run',
                '--disable-background-networking','--remote-debugging-address=127.0.0.1',
                '--remote-debugging-port=9226','--user-data-dir='+str(run/'browser-profile'),report['url']],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                for _ in range(100):
                    try:
                        response=httpx.get('http://127.0.0.1:9226/json/list',trust_env=False,timeout=1)
                        if any(t['type']=='page' and t['url'].startswith(report['url']) for t in response.json()):break
                    except (ValueError,httpx.HTTPError):pass
                    time.sleep(.2)
                output=Path('data/evaluation/question_bank')/('portable-browser-'+run.name)
                subprocess.run([shutil.which('node'),str(Path('scripts/verify_portable_ui.js').resolve()),
                    '9226',report['url'],str(output.resolve())],check=True,timeout=180)
                report['browser_report']=str(output/'report.json')
                checks['actual_browser_first_run_and_model_configuration']=True
            finally:
                browser.terminate();browser.wait(timeout=15)
        session=post('/api/session',{})['id'];course='便携包验证课程'
        # A genuine minimal OOXML file, converted to legacy DOC by the bundled LO.
        fixture=run/'fixture.docx'
        with zipfile.ZipFile(fixture,'w') as archive:
            archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+escape(line)+'</w:t></w:r></w:p>' for line in ['期末试题','一、证明该关系具有传递性，并说明证明步骤。'])+'</w:body></w:document>')
        subprocess.run([str(home/'tools/libreoffice/program/soffice.com'),
            '-env:UserInstallation='+(run/'fixture-profile').as_uri(), '--headless','--convert-to','doc:MS Word 97',
            '--outdir',str(run),str(fixture)],check=True,capture_output=True,timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        import pymupdf
        with pymupdf.open() as pdf:
            page=pdf.new_page();page.insert_text((50,70),'1. Calculate the rank of matrix A = [[1, 0], [0, 1]].')
            pdf_bytes=pdf.tobytes()
        files=[('示例课程-期末试题.md',(home/'示例资料/示例课程-期末试题.md').read_bytes()),
            ('fixture.docx',fixture.read_bytes()),('fixture.doc',(run/'fixture.doc').read_bytes()),('matrix.pdf',pdf_bytes)]
        response=client.post('/api/course-upload',data={'course':course,'session':session},
            files=[('files',(name,content)) for name,content in files]);response.raise_for_status()
        report['upload']=response.json();deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            status=get('/api/course-status',params={'course':course})
            if len(status['files'])==4 and not any(j['status'] in ('queued','running') for j in status['jobs']):break
            time.sleep(.25)
        else:raise TimeoutError('background document worker')
        report['documents']=status['files']
        checks['actual_background_worker_handles_md_pdf_docx_and_doc'] = all(f['status'] in ('ready','partial') and not f['error'] for f in status['files']) and status['coverage']['questions']>=3
        checks['word_previews_are_created_without_microsoft_word'] = all((home/'user-data/question_bank/processed'/str(f['id'])/'document.pdf').is_file() for f in status['files'] if f['name'].endswith(('.doc','.docx')))
        for item,(name,content) in zip(report['upload']['items'],files):
            response=client.get('/api/course-source',params={'course':course,'source':item['id']});response.raise_for_status()
            # Word's default source endpoint returns its derived PDF; raw bytes
            # are checked from retained originals, with the path owned by this run.
            originals=list((home/'user-data/question_bank/raw'/str(item['id'])).rglob(name))
            assert originals and originals[0].read_bytes()==content
        checks['all_uploaded_originals_are_retained'] = True
        model={'search_mode':'api','api':{'provider':'local','base_url':args.api_base,'api_key':key,
            'model':args.api_model,'images':False},'remember_keys':True}
        catalog=job('/api/model-catalog',model)
        checks['model_catalog_uses_real_api_without_codex'] = args.api_model in catalog['models']
        probe=job('/api/model-test',model)
        checks['real_api_json_connection_test'] = probe['ok']
        saved=job('/api/model-settings',model)
        checks['credentials_not_exposed_to_browser'] = saved['api']['has_key'] and 'api_key' not in saved['api']
        post('/api/setup-complete',{})
        answer=job('/api/chat',{'session':session,'question':'找等价关系的证明题，只要一道'})['reply']
        report['reply']=answer
        search=next(item['data'] for item in answer['skills'] if item['name']=='question_search')
        checks['real_api_second_round_selects_from_imported_questions'] = bool(answer['questions']) and any(call['status']=='ok' and call['phase']=='question_selection' for call in search['model_calls'])
        before=get('/api/history',params={'session':session})
        stop();after_boot=launch()
        after=get('/api/history',params={'session':session});restored=get('/api/model-settings')
        checks['model_key_course_and_chat_survive_stop_and_restart'] = restored['search_mode']=='api' and restored['api']['has_key'] and before==after and not after_boot['setup_required']
        checks['stop_bat_stops_only_this_copy'] = True
        stop()
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if process and process.poll() is None:
            try:client.post('/api/desktop-stop',json={});process.wait(timeout=20)
            except Exception:process.kill()
        if log:log.close()
        report['seconds']=round(time.monotonic()-started,2)
        report['passed']=bool(checks) and all(checks.values()) and 'error' not in report
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(report_path),'passed':report['passed'],'checks':checks,'error':report.get('error'),'seconds':report['seconds']},ensure_ascii=False,indent=2),flush=True)
    assert report['passed'],report.get('error',checks)


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');main()
