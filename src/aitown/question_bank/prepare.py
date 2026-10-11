"""Local extraction. Uncertain symbols and unsupported objects stay explicit."""
import re
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import COURSE_ID, ImageRegion, Page, ParsedSource, Question

TOPICS = {
    '集合运算': ['集合运算', '交集', '并集', '幂集', '对称差'],
    '等价关系': ['等价关系', '等价类', '等价'],
    '偏序': ['偏序', '哈斯', 'Hasse', '上界', '下界', '极大元', '极小元'],
    '函数': ['单射', '满射', '双射', '映射', '函数'],
    '基数': ['基数', '可数', '势', '无限集'],
    '连通性': ['连通', '割点', '割边'],
    '树': ['生成树', '树', '森林'],
    '欧拉图': ['欧拉', 'Euler'],
    '哈密顿图': ['哈密顿', 'Hamilton', '哈氏', '哈密尔顿'],
    '平面图': ['平面图', '平面性', '面数'],
    '着色': ['着色', '色数', '染色'],
    '匹配': ['匹配', 'Hall', '霍尔'],
    '关系': ['关系', '自反', '对称', '传递', '反自反', '反对称'],
    '图的度数': ['度数', '正则', '握手'],
}


def labels(text, course=COURSE_ID):
    topics = [name for name, words in TOPICS.items() if any(w.lower() in text.lower() for w in words)] if course==COURSE_ID else []
    qtype = next((kind for kind, words in [('判断', ['判断', '是否正确']), ('证明', ['证明', '证：', '求证']),
        ('选择', ['选择', '选项']), ('构造', ['构造', '举例']), ('计算', ['求', '计算', '画出'])] if any(w in text for w in words)), None)
    return topics, qtype


def metadata(text, source):
    header = text[:1200]
    years = set(re.findall(r'(?:19|20)\d{2}', header))
    year = int(next(iter(years))) if len(years) == 1 else None
    dated = re.search(r'(?:时间|日期|考试时间)[：:\s]*((?:19|20)\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]', header)
    if dated:
        year = int(dated[1])
    exam = 'final' if re.search('期末|期终|期終', header) else 'midterm' if '期中' in header else None
    category = 'answer' if source['category_hint'] == 'answer' else 'recollection' if source['category_hint'] == 'recollection' or re.search('回忆|记不清|去年的数据',text) else 'practice' if '练习' in header or '习题' in header else 'exam' if exam or '试卷' in header else 'unknown'
    return {'category': category, 'year': year, 'exam': exam}


BOUNDARY = re.compile(r'^\s*(?:([一二三四五六七八九十]+)[、．.]|(?:第\s*)?(\d{1,2})\s*[、．.](?!\d))\s*(\S.*)?$')
ANSWER_SECTION = re.compile(r'^\s*(?:[一二三四五六七八九十]+[、．.]\s*)?(?:参考答案|试题答案|答案与解析|参考解答|答案)\s*[:：]?\s*$')
ANSWER_START = re.compile(r'^\s*(?:解|答|解答|解析)\s*[:：]')


def order_pdf_headings(lines):
    # Word can emit a formula/right-hand fragment before the heading that
    # shares its baseline. Keep those overlapping pieces in the new question.
    chinese_mode=sum(bool((m:=BOUNDARY.match(row[3])) and m[1]) for row in lines)>=2
    result=list(lines)
    for heading in lines:
        match=BOUNDARY.match(heading[3])
        if not match or (chinese_mode and not match[1]):
            continue
        position=result.index(heading)
        overlapping=[i for i,row in enumerate(result[:position]) if row[0]==heading[0]
                     and row[1]<heading[2] and row[2]>heading[1]
                     and not BOUNDARY.match(row[3])]
        if overlapping:
            result.insert(min(overlapping),result.pop(position))
    return result


def split_lines(pages, lines, *, pdf=None, output=None, tight=False, course=COURSE_ID, boundaries=None, literal_lines=()):
    """Keep shared section instructions, all subparts, and continuation pages."""
    groups, current, section = [], None, ''
    chinese_mode = sum(bool((m := BOUNDARY.match(row[3])) and m[1]) for row in lines) >= 2
    in_answer = False
    for row in lines:
        ordinal, y0, y1, text = row[:4]
        heading = re.sub(r'^\s*#{1,6}\s+', '', text).rstrip(' #') if boundaries is not None else text
        if ordinal not in literal_lines and (ANSWER_SECTION.match(heading) or (boundaries is not None and ANSWER_SECTION.match(BOUNDARY.sub(lambda m: m[3] or '', heading)))):
            break
        match = boundaries.get(ordinal) if boundaries is not None else BOUNDARY.match(text)
        if boundaries is None and chinese_mode and match and not match[1]:
            match = None
        if match:
            if current:
                groups.append(current)
            number = match[1] or match[2]
            current = {'number': (section.split('、')[0]+'-' if section else '')+number, 'lines': [], 'section': section}
            in_answer = False
        if ordinal not in literal_lines and ANSWER_START.match(text):
            in_answer = True
        if current is not None and not in_answer:
            current['lines'].append(row)
    if current:
        groups.append(current)
    results, seen = [], {}
    for group in groups:
        rows = group['lines']
        if not rows:
            continue
        text = '\n'.join(([group['section']] if group['section'] else [])+[r[3] for r in rows]).strip()
        ordinals = list(dict.fromkeys(r[0] for r in rows))
        missing = []
        if '\ufffd' in text or any(0xE000 <= ord(c) <= 0xF8FF or 0x0B00 <= ord(c) <= 0x0D7F for c in text):
            missing.append('提取含未识别字符；公式与符号请核对原图')
        if re.search('记不清|去年的数据|数据.{0,5}不确定',text):
            missing.append('回忆者未确认部分题目条件；仅保留文件所载文字，不能视为原卷条件')
        topics, qtype = labels(text,course)
        images, image_regions = [], []
        if pdf is not None:
            import pymupdf as fitz
            regions = list(dict.fromkeys((r[0],*(r[4:6] if len(r)>4 else (0,pdf[r[0]-1].rect.width))) for r in rows))
            for region_index,(ordinal,x0,x1) in enumerate(regions):
                page = pdf[ordinal-1]
                selected = [r for r in rows if r[0] == ordinal and (len(r)==4 or (r[4],r[5])==(x0,x1))]
                if tight:
                    margins=set()
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines',[]):
                            content=''.join(s['text'] for s in line['spans'])
                            if line['bbox'][2]<page.rect.width*.12 and re.fullmatch(r'[\s（）()装订线内不要答题]+',content):
                                margins.add((round(line['bbox'][1],2),content))
                    selected=[r for r in selected if not re.match(r'^\s*第\s*\d+\s*页',r[3])
                        and (round(r[1],2),r[3]) not in margins]
                    if not selected:
                        continue
                # End at the next question/section, not the last text line, so graphs
                # below the question text remain visible.
                start = max(0, min(r[1] for r in selected)-3)
                following = [r[1] for r in lines if r[0] == ordinal and (len(r)==4 or (r[4],r[5])==(x0,x1))
                    and r[1] > max(x[1] for x in selected)+1
                    and (not tight or (round(r[1],2),r[3]) not in margins)]
                end = min(following)-2 if following else page.rect.height-12
                if end <= start:
                    end = page.rect.height
                if tight:
                    bottom = max(r[2] for r in selected)
                    # Native Word diagrams and embedded equation objects retain
                    # their PDF bounds. Keep them; omit empty answer space/footer.
                    objects = [fitz.Rect(d['rect']) for d in page.get_drawings()]
                    objects += [fitz.Rect(i['bbox']) for i in page.get_image_info()]
                    bottom = max([bottom, *[r.y1 for r in objects if r.y0 >= start and r.y1 < end
                        and r.height < page.rect.height*.9 and r.width < page.rect.width*.95]])
                    end = min(end, bottom+6)
                target = output / f'question-{len(results)+1}-page-{ordinal}-region-{region_index}.png'
                page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), clip=fitz.Rect(x0, start, x1, end)).save(target)
                images.append(str(target))
                image_regions.append(ImageRegion(index=len(images)-1,page=ordinal,bbox=[x0,start,x1,end]))
        else:
            for p in pages:
                if p.ordinal in ordinals and p.image:
                    images.append(p.image)
        if re.search('如图|下图|图示|图中', text) and not images:
            missing.append('题目引用图表，当前提取未能定位可显示图片')
        number = group['number']
        seen[number] = seen.get(number, 0)+1
        if seen[number] > 1:
            number += f'({seen[number]})'
        results.append(Question(number=number, text=text, pages=ordinals, images=images,image_regions=image_regions,
            subquestions=re.findall(r'(?:[（(][1-9a-z][)）]).*', text), topics=topics, question_type=qtype, missing=missing))
    return results


def split_markdown(pages, course=COURSE_ID):
    """Numbered headings delimit major questions; lists and deeper headings stay inside."""
    headings, plain, literal_lines = [], [], set()
    fence = None
    for page in pages:
        text = page.text
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', text)
        if marker:
            literal_lines.add(page.ordinal)
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if fence:
            literal_lines.add(page.ordinal)
            continue
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$', text)
        if heading and (match := BOUNDARY.match(heading[2])):
            headings.append((len(heading[1]), page.ordinal, match))
        elif match := BOUNDARY.match(text):
            plain.append((len(text)-len(text.lstrip()), page.ordinal, match))
    candidates = headings or plain
    level = min((item[0] for item in candidates), default=0)
    boundaries = {ordinal: match for depth, ordinal, match in candidates if depth == level}
    questions = split_lines(pages, [(p.ordinal, 0, 0, p.text) for p in pages],
                            course=course, boundaries=boundaries, literal_lines=literal_lines)
    for question in questions:
        question.subquestions = [part.strip() for part in re.findall(
            r'^(?:[ \t]*#{3,6}[ \t]+|[ \t]*)(?:[（(][1-9a-z][)）]|[a-z][)）]|\d+[.、])[ \t]*.+',
            question.text, re.MULTILINE)]
    return questions


def omit_unseparated_annotations(questions, notes):
    # Recollections sometimes insert solution notes in the middle of subparts.
    # Until reviewed, omitting the affected question also prevents its PDF crop
    # from exposing the note. The original source remains explicitly accessible.
    kept=[]
    for q in questions:
        if re.search(r'需要注意.{0,30}(?:实际上|正确答案|应为)',q.text):
            notes.append(f'原题号 {q.number} 含题中解题批注，题答尚未分离，暂不进入检索与图片预览')
        else:
            kept.append(q)
    return kept


def parse_pdf(path, source, output, config=None, *, tight=False):
    import pymupdf as fitz
    output.mkdir(parents=True, exist_ok=True)
    pages, lines = [], []
    with fitz.open(path) as document:
        for ordinal, page in enumerate(document, 1):
            image = output / f'page-{ordinal}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).save(image)
            text = page.get_text('text', sort=True)
            label = re.search(r'第\s*(\d+)\s*页\s*共\s*\d+\s*页',text)
            pages.append(Page(ordinal=ordinal, text=text, image=str(image),printed_label=label[1] if label else None))
            for block in page.get_text('dict', sort=True)['blocks']:
                for line in block.get('lines', []):
                    content = ''.join(s['text'] for s in line['spans'])
                    if tight and (re.match(r'^\s*第\s*\d+\s*页',content) or
                            (line['bbox'][2]<page.rect.width*.12 and re.fullmatch(r'[\s（）()装订线内不要答题]+',content))):
                        continue
                    if content.strip() and not re.match(r'^\s*第\s*\d+\s*页\s*共\s*\d+\s*页', content):
                        lines.append((ordinal, line['bbox'][1], line['bbox'][3], content))
        if tight:
            lines=order_pdf_headings(lines)
        notes = []
        empty_pages = [p.ordinal for p in pages if len(p.text.strip()) < 30]
        ocr_pages = []
        if empty_pages and config and config.pdf_ocr == 'windows':
            from .ocr import windows_ocr
            try:
                recognized = windows_ocr(document,empty_pages,output,columns=config.ocr_columns,timeout=config.ocr_timeout)
                ocr_pages = empty_pages
                lines = [row for row in lines if row[0] not in empty_pages]+recognized
                lines.sort(key=lambda row:row[0])  # Stable: preserve left then right region.
                for p in pages:
                    if p.ordinal in ocr_pages:
                        p.text='\n'.join(r[3] for r in recognized if r[0]==p.ordinal)
                notes.append('Windows 本地 OCR 已运行；数学符号、图表与题目边界尚未逐题审校')
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                notes.append(f'Windows 本地 OCR 未完成（{type(exc).__name__}）；保留原页，仍有文字缺失')
        meta = metadata('\n'.join(p.text for p in pages), source)
        questions = [] if meta['category'] == 'answer' else split_lines(pages, lines, pdf=document, output=output,tight=tight,course=source.get('course_id',COURSE_ID))
        from .layout import layout_traits
        for question in questions:
            question.dedup_layout = layout_traits(document,question)
    for q in questions:
        if set(q.pages)&set(ocr_pages):
            q.missing.append('OCR 文字未逐题审校，数学条件与图表请核对原题图')
    if ocr_pages:
        # General OCR can silently change mathematical conditions and merge headings.
        # Keep its page text and raw output for review, never publish guessed questions.
        questions = [q for q in questions if not set(q.pages)&set(ocr_pages)]
        notes.append('OCR 页文字仅供审校，未审校题目不进入检索；使用 review 导入核对后的题目')
    questions = omit_unseparated_annotations(questions,notes)
    if any(len(p.text.strip()) < 30 for p in pages):
        notes.append('部分页面无可用文字，保留原页；OCR 尚未完成或未能识别')
    if not questions and meta['category'] != 'answer':
        notes.append('未能可靠拆分题目，原页仍可核对')
    if meta['category'] == 'answer':
        notes.append('独立答案资料，未确认配对前不进入题干检索')
    return ParsedSource(pages=pages, questions=questions, status='partial' if notes or any(q.missing for q in questions) else 'ready', notes=notes, **meta)


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def math_text(node):
    if node is None:
        return ''
    if node.tag in (W+'t', M+'t'):
        return node.text or ''
    if node.tag == W+'r':
        value = ''.join(math_text(child) for child in node if child.tag != W+'rPr')
        alignment = node.find(W+'rPr/'+W+'vertAlign')
        if alignment is not None:
            position = alignment.attrib.get(W+'val')
            if position in ('superscript', 'subscript'):
                return ('^' if position == 'superscript' else '_')+'('+value+')'
        return value
    if node.tag == W+'sym':
        # Standard Adobe Symbol encoding; unknown glyphs remain explicit.
        mapping = {0x22:'∀',0x24:'∃',0x27:'∋',0x2A:'∗',0x2D:'−',0x40:'≅',0x44:'Δ',0x50:'Π',0x53:'Σ',
            0x61:'α',0x62:'β',0x64:'δ',0x65:'ε',0x66:'φ',0x6C:'λ',0x70:'π',0x73:'σ',
            0xA3:'≤',0xA5:'∞',0xAB:'↔',0xAC:'←',0xAE:'→',0xB3:'≥',0xB4:'×',0xB6:'∂',
            0xB8:'÷',0xB9:'≠',0xBA:'≡',0xBB:'≈',0xC6:'∅',0xC7:'∩',0xC8:'∪',0xC9:'⊃',
            0xCA:'⊇',0xCB:'⊄',0xCC:'⊂',0xCD:'⊆',0xCE:'∈',0xCF:'∉',0xD9:'∧',0xDA:'∨',
            0xDB:'⇔',0xDC:'⇐',0xDE:'⇒',0xE5:'∑'}
        code = int(node.attrib.get(W+'char', '0'), 16) & 0xff
        return mapping.get(code, '[未识别符号]') if node.attrib.get(W+'font') == 'Symbol' else '[未识别符号]'
    if node.tag in (W+'tab', W+'br'):
        return ' '
    if node.tag == M+'f':
        return '('+math_text(node.find(M+'num'))+')/('+math_text(node.find(M+'den'))+')'
    if node.tag in (M+'sSup', M+'sSub', M+'sSubSup'):
        base = math_text(node.find(M+'e'))
        return base+('_('+math_text(node.find(M+'sub'))+')' if node.find(M+'sub') is not None else '')+('^('+math_text(node.find(M+'sup'))+')' if node.find(M+'sup') is not None else '')
    if node.tag == M+'rad':
        return 'sqrt('+math_text(node.find(M+'e'))+')'
    if node.tag.endswith('Pr'):
        return ''
    value = ''.join(math_text(child) for child in node)
    while re.search(r'([\^_])\(([^()]*)\)\1\(([^()]*)\)',value):
        value = re.sub(r'([\^_])\(([^()]*)\)\1\(([^()]*)\)',r'\1(\2\3)',value)
    return value


def parse_docx(path, source, output):
    output.mkdir(parents=True, exist_ok=True)
    pages, lines, notes = [], [], []
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        relations = ET.fromstring(archive.read('word/_rels/document.xml.rels')) if 'word/_rels/document.xml.rels' in archive.namelist() else []
        rels = {r.attrib['Id']: r.attrib.get('Target', '') for r in relations if r.attrib.get('TargetMode') != 'External'}
        # Use real paragraph locators, never invent rendered page numbers.
        for ordinal, paragraph in enumerate(root.iter(W+'p'), 1):
            text = math_text(paragraph)
            if list(paragraph.iter(W+'object')):
                text += ' [嵌入公式或对象未提取]'
                notes.append(f'段落 {ordinal} 含旧式嵌入对象，公式需原件核对')
            images = []
            for blip in paragraph.iter(A+'blip'):
                member = 'word/'+rels.get(blip.attrib.get(R+'embed'), '')
                if member in archive.namelist() and member.startswith('word/media/'):
                    target = output / Path(member).name
                    target.write_bytes(archive.read(member))
                    images.append(str(target))
            pages.append(Page(ordinal=ordinal, text=text, image=images[0] if images else None, location_kind='paragraph'))
            if text.strip():
                lines.append((ordinal, 0, 0, text))
        meta = metadata('\n'.join(p.text for p in pages), source)
        questions = [] if meta['category'] == 'answer' else split_lines(pages, lines,course=source.get('course_id',COURSE_ID))
    for q in questions:
        if '[嵌入公式或对象未提取]' in q.text:
            q.missing.append('旧式嵌入公式未提取；请打开原 DOCX 核对数学条件')
        if '[未识别符号]' in q.text:
            q.missing.append('Symbol 字体外的符号未能识别，请核对原件')
    questions = omit_unseparated_annotations(questions,notes)
    if not questions and meta['category'] != 'answer':
        notes.append('未能可靠识别题目边界')
    if meta['category'] == 'answer':
        notes.append('答案独立保存，未确认配对前不用于题目答案')
    return ParsedSource(pages=pages, questions=questions, status='partial' if notes or any(q.missing for q in questions) else 'ready', notes=notes, **meta)


def parse_file(path: Path, source, output, config=None):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        if config and config.extractor == 'mineru_api':
            from .mineru_api import parse_mineru_api_pdf,validate_source
            try:
                validate_source(source)
            except ValueError:
                parsed=parse_pdf(path,source,output,config)
                parsed.notes.append('此来源未获 MinerU 上传授权，已改用本地 PDF 解析；未上传文件')
                parsed.status='partial'
                return parsed
            return parse_mineru_api_pdf(path,source,output,config)
        if config and config.extractor == 'mineru':
            from .mineru import parse_mineru_pdf
            return parse_mineru_pdf(path,source,output,config)
        return parse_pdf(path, source, output, config)
    if suffix in ('.docx', '.doc'):
        from .word import parse_word
        return parse_word(path, source, output, config)
    if suffix in ('.txt', '.md', '.html', '.htm'):
        if suffix in ('.html', '.htm'):
            from .supplements import read_web
            text, _ = read_web(path, source.get('resolved_url') or source['url'])
        else:
            text = path.read_text(encoding='utf-8-sig')
        pages = [Page(ordinal=n, text=t, location_kind='line') for n, t in enumerate(text.splitlines(), 1)]
        meta=metadata(text,source)
        notes=[]
        if suffix in ('.html','.htm'):
            warning='网页按文字整理，公式、表格和图片排版需打开来源核对'
            notes.append(warning)
        if meta['category']=='answer':
            questions = []
        elif suffix in ('.md', '.html', '.htm'):
            questions = split_markdown(pages, source.get('course_id',COURSE_ID))
        else:
            questions = split_lines(pages, [(p.ordinal, 0, 0, p.text) for p in pages],course=source.get('course_id',COURSE_ID))
        questions = omit_unseparated_annotations(questions,notes)
        if suffix in ('.html','.htm'):
            for question in questions:question.missing.append(warning)
        if meta['category']=='answer':notes.append('独立答案资料；未确认配对前不进入题干检索')
        elif not questions:notes.append('正文已保存，但未识别带题号的独立题目；可打开原件或补充网页中的资料附件')
        return ParsedSource(pages=pages, questions=questions, status='partial' if notes or not questions else 'ready',notes=notes, **meta)
    return ParsedSource(pages=[], questions=[], status='unsupported', notes=[f'当前不支持 {suffix} 正文解析，保留原文件'])
