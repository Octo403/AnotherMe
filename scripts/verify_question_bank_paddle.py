"""Prepare matched real scan inputs for Windows/Paddle comparison, without reimporting."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import pymupdf as fitz

from aitown.question_bank.models import BankConfig
from aitown.question_bank.ocr import windows_ocr
from aitown.question_bank.repository import BankRepository


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--python',type=Path,default=Path('.venv-paddle/Scripts/python.exe'))
    parser.add_argument('--timeout',type=float,default=1800)
    args=parser.parse_args()
    output=Path('data/evaluation/question_bank/paddle');output.mkdir(parents=True,exist_ok=True)
    manifest=[];windows=[];timings=[]
    with BankRepository(BankConfig().database,readonly=True) as repo:
        for sid,ordinals,columns in ((879,[1,2],2),(785,[1],1),(786,[1],1)):
            source=repo.source(sid);folder=output/str(sid)
            started=time.perf_counter()
            with fitz.open(source['local_path']) as document:
                rows=windows_ocr(document,ordinals,folder,columns=columns)
                requests=json.loads((folder/'ocr/request.json').read_text(encoding='utf-8'))
                for request in requests:
                    ordinal,column=map(int,request['key'].split('-'))
                    sample={**request,'key':f'{sid}-{ordinal}-{column}','source_id':sid,'page':ordinal,
                        'column':column,'source_path':source['path'],'member':source['member']}
                    manifest.append(sample)
                    text='\n'.join(r[3] for r in rows if r[0]==ordinal and round(r[4]/document[ordinal-1].rect.width*columns)==column)
                    windows.append({**sample,'text':text})
            seconds=time.perf_counter()-started;timings.append({'source_id':sid,'seconds':seconds})
            print(f'Windows sample source {sid}: {seconds:.2f}s',flush=True)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'windows.json').write_text(json.dumps(windows,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'windows-run.json').write_text(json.dumps({'timings':timings,'seconds':sum(t['seconds'] for t in timings)},indent=2),encoding='utf-8')
    if args.run:
        subprocess.run([str(args.python.resolve()),'scripts/paddle_extract.py','--manifest',str(output/'manifest.json'),
            '--output',str(output/'output')],check=True,timeout=args.timeout)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
