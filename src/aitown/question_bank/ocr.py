"""Optional local Windows OCR. Coordinates refer to original physical PDF pages."""
import json
import os
import re
import subprocess
from pathlib import Path


def windows_ocr(document, ordinals, output, *, columns=1, timeout=180):
    if os.name != 'nt':
        raise ValueError('Windows OCR 仅可在安装了中文 OCR 语言包的 Windows 上运行')
    import pymupdf as fitz
    bundle = output/'ocr'
    bundle.mkdir(parents=True, exist_ok=True)
    requests, regions = [], {}
    scale = 2.5
    for ordinal in ordinals:
        page = document[ordinal-1]
        for column in range(columns):
            x0, x1 = page.rect.width*column/columns, page.rect.width*(column+1)/columns
            key = f'{ordinal}-{column}'
            target = bundle/f'page-{key}.png'
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(x0,0,x1,page.rect.height)).save(target)
            requests.append({'key':key, 'path':str(target.resolve())})
            regions[key] = (ordinal,x0,x1)
    request = bundle/'request.json'
    response = bundle/'response.json'
    request.write_text(json.dumps(requests,ensure_ascii=False),encoding='utf-8')
    runner = Path(__file__).resolve().parents[3]/'scripts'/'windows_ocr.ps1'
    if not runner.is_file():
        runner = Path(__file__).with_name('windows_ocr.ps1')
    process = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(runner),'-RequestFile',str(request.resolve()),'-OutputFile',str(response.resolve())],
        capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise ValueError('Windows 本地 OCR 失败；请检查中文 OCR 语言包或单独运行 scripts/windows_ocr.ps1')
    result = json.loads(response.read_text(encoding='utf-8-sig'))
    found = {item['key']:item for item in result}
    if set(found) != set(regions):
        raise ValueError('OCR 返回页数与请求不一致')
    rows = []
    for key,(ordinal,x0,x1) in regions.items():
        page = document[ordinal-1]
        for line in sorted(found[key]['lines'],key=lambda line:(line['bbox'][1],line['bbox'][0])):
            y0,y1 = line['bbox'][1]/scale,line['bbox'][3]/scale
            text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])','',line['text']).strip()
            if re.fullmatch(r'\d+',text) and y0 > page.rect.height*.90:
                continue  # Printed page labels must not join the last question.
            rows.append((ordinal,y0,y1,text,x0,x1))
    return rows
