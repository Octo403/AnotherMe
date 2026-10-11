"""Optional isolated MinerU SDK bridge. Original PDF crops remain authoritative."""
import json
import re
import subprocess
from html import unescape
from pathlib import Path

from .models import COURSE_ID, Page, ParsedSource
from .prepare import metadata, split_lines, omit_unseparated_annotations


def parse_mineru_pdf(path, source, output, config):
    import pymupdf as fitz
    if not config.mineru_python or not config.mineru_python.is_file():
        raise ValueError('MinerU 独立 Python 不存在，请配置 MINERU_PYTHON')
    output.mkdir(parents=True,exist_ok=True)
    bundle = output/'mineru'
    runner = Path(__file__).resolve().parents[3]/'scripts'/'mineru_extract.py'
    process = subprocess.run([str(config.mineru_python.resolve()),str(runner),str(path.resolve()),
        str(bundle.resolve()),'--tier',config.mineru_tier],capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=config.mineru_timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name=='nt' else 0)
    if process.returncode:
        raise ValueError(f'MinerU 本地提取失败，退出码 {process.returncode}；请单独运行 scripts/mineru_extract.py 查看错误')
    content = json.loads((bundle/'structured_content.json').read_text(encoding='utf-8'))
    notes=[f'MinerU 本地 {config.mineru_tier} 提取；公式与版面尚未逐题核对，原图为核对依据']
    return parsed_content(path, source, output, content, notes)


def parsed_content(path, source, output, content, notes):
    import pymupdf as fitz
    pages,lines=[],[]
    with fitz.open(path) as document:
        indices=[int(p['page_idx']) for p in content['pages']]
        if not indices or len(indices)!=len(set(indices)) or any(i<0 or i>=len(document) for i in indices):
            raise ValueError('MinerU 返回的 PDF 页码缺失、重复或超出原卷范围')
        for page in content['pages']:
            ordinal = int(page['page_idx'])+1
            original = document[ordinal-1]
            image=output/f'page-{ordinal}.png'
            original.get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(image)
            texts=[]
            for block in page['blocks']:
                if block['type'] in ('header','footer'):
                    continue
                text=block.get('content','')
                if not isinstance(text,str):
                    raise ValueError('无法识别的 MinerU 内容类型')
                text=unescape(re.sub(r'<[^>]+>','',text)).replace('**','')
                if not text.strip() and block['type'] in ('equation','image','table'):
                    text='[公式或图表见原图]'
                if not text.strip():
                    continue
                bbox=block.get('bbox')
                if not bbox or len(bbox)!=4:
                    raise ValueError('MinerU PDF 块缺少区域，无法可靠关联原题图片')
                if not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in bbox) or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                    raise ValueError('MinerU PDF 区域坐标无效，无法可靠关联原题图片')
                texts.append(text)
                for line in text.splitlines():
                    lines.append((ordinal,bbox[1]*original.rect.height,bbox[3]*original.rect.height,line))
            pages.append(Page(ordinal=ordinal,text='\n'.join(texts),image=str(image)))
        meta=metadata('\n'.join(p.text for p in pages),source)
        questions=[] if meta['category']=='answer' else split_lines(pages,lines,pdf=document,output=output,course=source.get('course_id',COURSE_ID))
    questions=omit_unseparated_annotations(questions,notes)
    for q in questions:
        q.missing.append('MinerU 提取未逐题核对，请同时查看原题图')
        if '[公式或图表见原图]' in q.text:
            q.missing.append('公式或图表仅保留原图，文本不完整')
    return ParsedSource(pages=pages,questions=questions,status='partial',notes=notes,**meta)
