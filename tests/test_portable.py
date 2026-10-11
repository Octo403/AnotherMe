import json
from contextlib import closing
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.portable import configure_environment, running_instance
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def test_portable_does_not_inherit_developer_paths_or_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('LLM_API_KEY', 'developer-key')
    monkeypatch.setenv('LOCAL_EMBEDDING_DIR', 'private-model')
    monkeypatch.setenv('QUESTION_BANK_DIR', 'private-bank')
    monkeypatch.setenv('PYTHONPATH', 'developer-source')
    monkeypatch.setenv('MINERU_API_KEY', 'developer-mineru-key')
    # Undo all environment mutations after this test, including new variables.
    monkeypatch.setattr('os.environ', dict(__import__('os').environ))
    configure_environment(tmp_path)
    import os
    assert os.getcwd() == str(tmp_path)
    assert not any(key in os.environ for key in ('LLM_API_KEY','LOCAL_EMBEDDING_DIR','QUESTION_BANK_DIR','MINERU_API_KEY','PYTHONPATH'))
    assert os.environ['AITOWN_PORTABLE_HOME'] == str(tmp_path)


def test_setup_choice_and_encrypted_model_preferences_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('AITOWN_PORTABLE_HOME', str(tmp_path))
    settings = settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'), tmp_path/'app')
    metadata = tmp_path/'ui.sqlite3'
    with closing(UIService(settings, metadata)) as service:
        assert service.call('_bootstrap')['setup_required']
        details = service.call('_model_settings')['documents']
        assert details['pdf'] and details['docx_text'] and details['directory'] == str((tmp_path/'bank').resolve())
        service.call('_setup_complete')
    with closing(UIService(settings, metadata)) as service:
        assert not service.call('_bootstrap')['setup_required']


def test_desktop_stop_only_on_portable_server_and_same_origin(tmp_path):
    import threading
    settings = settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'app')
    with closing(UIService(settings,tmp_path/'ui.sqlite3')) as service:
        server = make_server(service,0,desktop={'directory':str(tmp_path)})
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            (tmp_path/'user-data').mkdir()
            (tmp_path/'user-data/desktop.json').write_text(json.dumps({'port':server.server_port}))
            url = running_instance(tmp_path.resolve())
            assert url == f'http://127.0.0.1:{server.server_port}'
            assert running_instance(tmp_path/'different') is None
            with httpx.Client(base_url=url,trust_env=False) as client:
                assert client.get('/setup.js').status_code == 200
                assert client.post('/api/desktop-stop',json={},headers={'Origin':'https://example.org'}).status_code == 403
                assert client.post('/api/desktop-stop',json={}).json()['stopping']
            thread.join(timeout=5)
            assert not thread.is_alive()
        finally:
            server.shutdown();server.server_close();thread.join()


def test_libreoffice_conversion_validates_pdf_and_disables_macros(tmp_path, monkeypatch):
    import subprocess
    import pymupdf
    from aitown.question_bank import word
    source = tmp_path/'中文 test.doc';source.write_bytes(b'synthetic')
    monkeypatch.setattr(word,'libreoffice_command',lambda:'owned-converter')
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            assert command[0] == 'owned-converter' and '--headless' in command
            from urllib.request import url2pathname
            profile = Path(url2pathname(command[1].split('=',2)[-1][7:]))
            assert '<value>3</value>' in (profile/'user/registrymodifications.xcu').read_text()
            output = Path(command[command.index('--outdir')+1])
            with pymupdf.open() as pdf:
                pdf.new_page();pdf.save(output/(Path(command[-1]).stem+'.pdf'))
        def communicate(self, timeout):return b'', b''
    monkeypatch.setattr(subprocess,'Popen',Process)
    target = word.export_libreoffice(source,tmp_path/'out')
    with pymupdf.open(target) as pdf:assert len(pdf)==1
    assert source.read_bytes()==b'synthetic'


def test_docx_without_converter_keeps_readable_text(tmp_path, monkeypatch):
    from aitown.question_bank.repository import BankRepository
    row={'path':'example.docx','member':'','rendered_text':'','pages':'[1]','subquestions':'[]',
        'topics':'[]','images':'[]','missing':'[]','text':'证明该关系具有传递性'}
    assert BankRepository.question_dict(row)['content_format']=='plain'
