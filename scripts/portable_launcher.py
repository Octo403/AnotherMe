"""Standard-library bootstrap copied beside Start.bat in the Windows bundle."""
import argparse
import faulthandler
import importlib
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--diagnose', action='store_true')
    args, remaining = parser.parse_known_args()
    home = args.home.resolve()
    log_path = home/'user-data/startup.log'
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open('a', encoding='utf-8', buffering=1)
    except OSError:
        log_path = Path(tempfile.gettempdir())/f'AnotherMe-startup-{os.getpid()}.log'
        log = log_path.open('a', encoding='utf-8', buffering=1)
    with log:
        def progress(message, *, ready=False):
            if ready:
                faulthandler.cancel_dump_traceback_later()
            print(message, flush=True)
            log.write(time.strftime('%Y-%m-%d %H:%M:%S ')+message+'\n')
        progress('\n[2/5] Python 已启动：'+sys.version.split()[0]+
                 '\n启动日志：'+str(log_path))
        log.write('Windows: '+str(sys.getwindowsversion() if hasattr(sys,'getwindowsversion') else sys.platform)+
                  '\nArchitecture: '+os.environ.get('PROCESSOR_ARCHITECTURE','unknown')+
                  '\nHome: '+str(home)+'\n')
        progress('若某个阶段超过 30 秒未推进，可查看日志中的线程位置；请勿删除 user-data。')
        # Observe slow imports and native crashes; never kill a slow startup.
        faulthandler.enable(file=log, all_threads=True)
        faulthandler.dump_traceback_later(30, repeat=True, file=log)
        try:
            if args.diagnose:
                for name in ('httpx','numpy','scipy','faiss','pymupdf','aitown.ui.service'):
                    progress('检查依赖：'+name)
                    importlib.import_module(name)
                progress('诊断完成：运行环境依赖可以加载。此检查未启动服务或读取课程资料。', ready=True)
                return
            portable = importlib.import_module('aitown.portable')
            sys.argv = [sys.argv[0], '--home', str(home), *remaining]
            portable.main(progress=progress)
        except SystemExit as exc:
            if exc.code:
                progress('启动进程退出，错误码：'+str(exc.code)+'。请同时查看 startup-error.log。')
            raise
        except Exception:
            details = traceback.format_exc()
            log.write(details+'\n')
            try:
                (home/'user-data/startup-error.log').write_text(details, encoding='utf-8')
            except OSError:
                pass
            progress('启动失败，详细错误已保存：'+str(log_path))
            raise SystemExit(1) from None
        finally:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()


if __name__ == '__main__':
    main()
