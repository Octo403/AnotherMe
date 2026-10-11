"""Relocatable local desktop entry point. No system Python, .env or CLI login."""
import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
import webbrowser
from pathlib import Path

def configure_environment(home):
    # A downloaded copy must not pick up the developer's corpus or model paths.
    for key in list(os.environ):
        if key.startswith(('LLM_', 'QUESTION_', 'MINERU_', 'LOCAL_EMBEDDING_', 'LOCAL_RERANKER_')):
            os.environ.pop(key, None)
    os.environ.pop('PYTHONPATH', None)
    os.environ.pop('PYTHONHOME', None)
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONNOUSERSITE'] = '1'
    os.environ['AITOWN_PORTABLE_HOME'] = str(home)
    os.environ['AITOWN_SOFFICE'] = str(home/'tools/libreoffice/program/soffice.com')
    # The optional Windows OCR helper and 7-Zip are shipped with the runtime.
    os.environ['PATH'] = str(home/'tools/7zip')+os.pathsep+os.environ.get('PATH', '')
    os.chdir(home)


def running_instance(home):
    import httpx
    try:
        record = json.loads((home/'user-data/desktop.json').read_text(encoding='utf-8'))
        port = int(record['port'])
        if not 1 <= port <= 65535:
            return None
        with httpx.Client(trust_env=False, timeout=1) as client:
            response = client.get(f'http://127.0.0.1:{port}/api/desktop')
            if response.status_code != 200:
                return None
            value = response.json()
        if Path(value['directory']).resolve() == home:
            return f'http://127.0.0.1:{port}'
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        pass
    return None


def open_browser_page(url):
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print('未能自动打开浏览器。服务仍可使用，请手动打开：'+url, flush=True)
    return opened


def run(home, *, port=8501, open_browser=True, stop=False, progress=None):
    def report(message, *, ready=False):
        if progress:
            progress(message, ready=ready)
        else:
            print(message, flush=True)
    home = Path(home).resolve()
    report('[3/5] 检查本地资料目录与已有服务……')
    directory = home/'user-data'
    directory.mkdir(parents=True, exist_ok=True)
    existing = running_instance(home)
    if stop:
        if existing:
            import httpx
            with httpx.Client(trust_env=False, timeout=5) as client:
                client.post(existing+'/api/desktop-stop', json={}).raise_for_status()
            print('AnotherMe 已收到停止请求。', flush=True)
        else:
            print('当前体验包没有正在运行的服务。', flush=True)
        return
    if existing:
        report('AnotherMe 已在运行：'+existing, ready=True)
        if open_browser:
            open_browser_page(existing)
        return
    # A normal SQLite write transaction prevents simultaneous double clicks from
    # creating two services for the same data directory. It is not an app DB lock.
    owner = sqlite3.connect(directory/'launcher.sqlite3', timeout=1)
    try:
        try:
            owner.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError:
            for _ in range(100):
                existing = running_instance(home)
                if existing:
                    report('AnotherMe 已在运行：'+existing, ready=True)
                    if open_browser:
                        open_browser_page(existing)
                    return
                time.sleep(.2)
            raise RuntimeError('另一个启动进程正在初始化。请稍候再双击启动。') from None
        configure_environment(home)
        report('[4/5] 加载界面与计算依赖，首次启动可能需要稍等……')
        from .config import Settings
        from .ui.demo import settings_for_ui
        from .ui.server import make_server
        from .ui.service import UIService
        report('[5/5] 初始化本地数据库并启动网页服务……')
        installed = Settings(question_bank_dir=directory/'question_bank')
        settings = settings_for_ui(installed, directory/'application')
        service = UIService(settings, directory/'application/ui.sqlite3')
        server = None
        try:
            for candidate in range(port, min(port+20, 65536)) if port else [0]:
                try:
                    server = make_server(service, candidate, desktop={'directory':str(home)})
                    break
                except OSError as exc:
                    if getattr(exc, 'winerror', None) != 10048 and exc.errno not in (48, 98, 10048):
                        raise
            if server is None:
                raise RuntimeError('本机端口已占用，请关闭其他体验版或指定其他端口。')
            record = {'port':server.server_port, 'pid':os.getpid()}
            (directory/'desktop.json').write_text(json.dumps(record), encoding='utf-8')
            url = f'http://127.0.0.1:{server.server_port}'
            report('AnotherMe 已启动：'+url+'\n资料保存位置：'+str(directory)+
                '\n请保留此窗口；关闭窗口或双击 Stop.bat 可停止界面。', ready=True)
            # The listening socket is already bound; the request waits until the
            # loop below begins. First-run guidance is decided by saved settings.
            if open_browser:
                # A stalled Windows browser association must not block serving.
                import threading
                threading.Thread(target=open_browser_page,args=(url,),daemon=True).start()
            try:
                server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:
                pass
        finally:
            if server:
                server.server_close()
            service.close()
    finally:
        owner.close()


def main(*, progress=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='AnotherMe Windows 本地体验版')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('端口须在 0–65535 之间')
    try:
        run(args.home, port=args.port, open_browser=not args.no_browser, stop=args.stop, progress=progress)
    except Exception:
        if progress:
            # The standard-library bootstrap owns diagnostics, including errors
            # before imports and when this directory cannot hold a log.
            raise
        log = args.home.resolve()/'user-data/startup-error.log'
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass
        print('启动失败。请把压缩包完整解压到可写目录，并查看：'+str(log), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
