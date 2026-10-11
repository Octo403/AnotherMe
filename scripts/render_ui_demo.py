"""Render the local UI; personal demonstration reports/screenshots belong in data/private."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('report',type=Path)
    parser.add_argument('--chrome',type=Path,default=Path('C:/Program Files/Google/Chrome/Application/chrome.exe'))
    args=parser.parse_args()
    report=json.loads(args.report.read_text(encoding='utf-8'))
    root=args.report.parent;output=root/'screenshots';output.mkdir(exist_ok=True)
    profile=(Path('data/runtime/t12-render')/root.name).resolve();profile.mkdir(parents=True,exist_ok=True)
    captures=[]
    for name,url in report['screenshots'].items():
        target=(output/(name+'.png')).resolve()
        completed=subprocess.run([str(args.chrome),'--headless=new','--disable-gpu','--no-first-run',
            '--disable-background-networking','--disable-component-update','--disable-breakpad',
            '--no-default-browser-check','--user-data-dir='+str(profile), '--window-size=1440,980',
            '--virtual-time-budget=8000','--screenshot='+str(target),url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        captures.append({'name':name,'file':target.relative_to(Path.cwd()).as_posix(),
                         'exit_code':completed.returncode,'rendered':target.exists()})
        if not target.exists():raise RuntimeError('Chrome did not render '+name)
    mobile=(output/'mobile.png').resolve()
    completed=subprocess.run([str(args.chrome),'--headless=new','--disable-gpu','--no-first-run',
        '--disable-background-networking','--disable-component-update','--disable-breakpad',
        '--no-default-browser-check','--user-data-dir='+str(profile),'--window-size=520,900',
        '--virtual-time-budget=8000','--screenshot='+str(mobile),report['screenshots']['sources']],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    captures.append({'name':'mobile','file':mobile.relative_to(Path.cwd()).as_posix(),
                     'exit_code':completed.returncode,'rendered':mobile.exists()})
    if not mobile.exists():raise RuntimeError('Chrome did not render mobile')
    (root/'render.json').write_text(json.dumps({'method':'installed Chrome headless rendering of actual HTTP UI',
        'interactive_browser_control':False,'captures':captures},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(captures,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
