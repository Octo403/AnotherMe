import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from aitown import portable


def load_launcher():
    path = Path(__file__).resolve().parents[1]/'scripts/portable_launcher.py'
    spec = importlib.util.spec_from_file_location('portable_launcher_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launcher(monkeypatch):
    module = load_launcher()
    # The real faulthandler is exercised in a separate process by the ZIP check;
    # don't change pytest's own crash handler in this process.
    for name in ('enable','disable','dump_traceback_later','cancel_dump_traceback_later'):
        monkeypatch.setattr(module.faulthandler,name,lambda *a,**k:None)
    return module


def test_import_failure_has_early_output_and_traceback(launcher,tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['launcher.py','--home',str(tmp_path)])
    def missing(name):
        raise ModuleNotFoundError('synthetic native dependency unavailable')
    monkeypatch.setattr(launcher.importlib,'import_module',missing)
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert 'Python' in output and 'startup.log' in output
    assert 'ModuleNotFoundError' in (tmp_path/'user-data/startup.log').read_text(encoding='utf-8')
    assert 'synthetic native dependency unavailable' in (tmp_path/'user-data/startup-error.log').read_text(encoding='utf-8')


def test_unwritable_home_reports_temporary_log(launcher,tmp_path,monkeypatch,capsys):
    home=tmp_path/'read-only';home.mkdir()
    fallback=tmp_path/'temporary';fallback.mkdir()
    monkeypatch.setattr(sys,'argv',['launcher.py','--home',str(home)])
    monkeypatch.setattr(launcher.tempfile,'gettempdir',lambda:str(fallback))
    original_open=Path.open
    def open_file(path,*args,**kwargs):
        if path.is_relative_to(home):raise PermissionError('synthetic read-only directory')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_file)
    def fail_main(**kwargs):raise PermissionError('synthetic read-only directory')
    monkeypatch.setattr(launcher.importlib,'import_module',lambda name:SimpleNamespace(main=fail_main))
    with pytest.raises(SystemExit):launcher.main()
    logs=list(fallback.glob('AnotherMe-startup-*.log'))
    assert len(logs)==1 and 'PermissionError' in logs[0].read_text(encoding='utf-8')
    assert str(logs[0]) in capsys.readouterr().out


@pytest.mark.parametrize('raises',[False,True])
def test_browser_failure_keeps_manual_url_available(monkeypatch,capsys,raises):
    def unavailable(url):
        if raises:raise OSError('synthetic browser association failure')
        return False
    monkeypatch.setattr(portable.webbrowser,'open',unavailable)
    assert portable.open_browser_page('http://127.0.0.1:12345') is False
    assert 'http://127.0.0.1:12345' in capsys.readouterr().out


def test_diagnose_loads_runtime_without_starting_service(launcher,tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['launcher.py','--home',str(tmp_path),'--diagnose'])
    imported=[]
    monkeypatch.setattr(launcher.importlib,'import_module',lambda name:imported.append(name))
    launcher.main()
    assert 'faiss' in imported and 'aitown.ui.service' in imported
    assert not (tmp_path/'user-data/desktop.json').exists()
    assert not (tmp_path/'user-data/application').exists()
    assert '诊断完成' in capsys.readouterr().out
