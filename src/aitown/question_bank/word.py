"""Native, local Word print-layout conversion; originals and paragraph IDs survive."""
import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _stop_owned_word(record):
    """Timeout cleanup uses the recorded process creation time, never all WINWORDs."""
    try:
        identity = json.loads(record.read_text(encoding='utf-8-sig'))
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x0001 | 0x0400, False, int(identity['pid']))
        if not handle:
            return
        try:
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if kernel.GetProcessTimes(ctypes.c_void_p(handle), *(ctypes.byref(t) for t in times)):
                if times[0].value == int(identity['created']):
                    kernel.TerminateProcess(ctypes.c_void_p(handle), 1)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    except (OSError, ValueError, KeyError):
        pass


def _export_native_word(path, output, timeout=180):
    if os.name != 'nt':
        raise OSError('Word 原排版转换需要 Windows 和已安装的 Microsoft Word')
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'document.pdf'
    with tempfile.TemporaryDirectory(prefix='word-export-', dir=output) as temporary:
        temporary = Path(temporary)
        pdf, record = temporary/'document.pdf', temporary/'owned-word.json'
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(Path(__file__).with_name('export_word.ps1')),
                   '-InputPath', str(path.resolve()), '-OutputPath', str(pdf.resolve()),
                   '-ProcessRecord', str(record.resolve())]
        try:
            result = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_owned_word(record)
            raise TimeoutError('Word 转 PDF 超时；请检查文件是否加密、损坏或 Word 首次启动弹窗') from None
        if result.returncode or not pdf.is_file():
            _stop_owned_word(record)
            # Office output may use the system code page. Keep a bounded diagnostic.
            detail = result.stderr.decode('utf-8', errors='replace')[-1200:]
            raise OSError('Word 转 PDF 失败：'+detail)
        import pymupdf
        with pymupdf.open(pdf) as document:
            if not len(document):
                raise ValueError('Word 导出的 PDF 没有页面')
        pdf.replace(target)
    return target


def libreoffice_command():
    """Prefer the bundled converter; never require a system Office installation."""
    candidates = [os.getenv('AITOWN_SOFFICE', '')]
    portable = os.getenv('AITOWN_PORTABLE_HOME')
    if portable:
        candidates.append(str(Path(portable)/'tools/libreoffice/program/soffice.com'))
    candidates += [shutil.which('soffice') or '', shutil.which('soffice.com') or '']
    for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
        if os.getenv(variable):
            candidates.append(str(Path(os.environ[variable])/'LibreOffice/program/soffice.com'))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def export_libreoffice(path, output, timeout=180):
    executable = libreoffice_command()
    if not executable:
        raise OSError('未找到 LibreOffice 转换器；可使用完整版体验包，或将 DOC 另存为 PDF / DOCX 后上传')
    output.mkdir(parents=True, exist_ok=True)
    # Office converters still hit Windows MAX_PATH in nested extraction folders.
    # Use a short owned working directory and copy back only a validated PDF.
    with tempfile.TemporaryDirectory(prefix='aitown-office-') as temporary:
        temporary = Path(temporary).resolve()
        document_path = temporary/('document'+path.suffix.lower())
        shutil.copyfile(path, document_path)
        profile = temporary/'profile'
        (profile/'user').mkdir(parents=True)
        # Untrusted course documents must not run macros. This profile belongs to
        # this conversion only, so it cannot inherit trusted user locations.
        (profile/'user/registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>', encoding='utf-8')
        command = [executable, '-env:UserInstallation='+profile.as_uri(), '--headless',
            '--nologo', '--nodefault', '--norestore', '--convert-to', 'pdf:writer_pdf_Export',
            '--outdir', str(temporary), str(document_path)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                else:
                    process.kill()
            process.communicate(timeout=10)
            raise TimeoutError('本地文档转换超时；原文件已保留，请检查加密或损坏情况') from None
        pdf = temporary/'document.pdf'
        if process.returncode or not pdf.is_file():
            raise OSError('LibreOffice 转 PDF 失败；原文件已保留。'+(stderr or stdout).decode('utf-8', errors='replace')[-500:])
        import pymupdf
        with pymupdf.open(pdf) as document:
            if not len(document):
                raise ValueError('文档转换 PDF 没有页面')
        target = output/'document.pdf'
        pending = output/'document.pdf.tmp'
        shutil.copyfile(pdf, pending)
        pending.replace(target)
    return target


def export_word(path, output, timeout=180):
    if os.getenv('AITOWN_PORTABLE_HOME') or os.getenv('AITOWN_SOFFICE'):
        return export_libreoffice(path, output, timeout)
    try:
        return _export_native_word(path, output, timeout)
    except (OSError, TimeoutError):
        if not libreoffice_command():
            raise
        return export_libreoffice(path, output, timeout)


def _chinese_anchor(text):
    text = re.sub(r'\[(?:嵌入公式或对象未提取|未识别符号)\]', '', text)
    return ''.join(re.findall(r'[\u4e00-\u9fff]', text))


def attach_word_layout(original, rendered):
    """Match complete question headings and Chinese text, never just a paragraph number."""
    original.rendered_pages = rendered.pages
    lookup = {q.number: q for q in rendered.questions}
    count = 0
    for question in original.questions:
        candidate = lookup.get(question.number)
        anchor = _chinese_anchor(question.text)
        if not candidate or len(anchor) < 8 or anchor != _chinese_anchor(candidate.text):
            original.notes.append(f'原题号 {question.number} 未能确认在转换 PDF 中的完整边界，保留原文件入口')
            continue
        question.images = candidate.images
        question.image_regions = candidate.image_regions
        # Exact exported text allows DOCX/PDF occurrences of the same question to
        # share a result without erasing case, exponents or mathematical spacing.
        question.rendered_text = candidate.text
        question.dedup_layout = candidate.dedup_layout
        count += 1
    original.notes.append(f'本地文档转 PDF；已定位并裁剪 {count}/{len(original.questions)} 道题；原段落定位保留')
    return original


def parse_word(path, source, output, config=None):
    from .prepare import parse_docx, parse_pdf
    original = parse_docx(path, source, output) if path.suffix.lower() == '.docx' else None
    try:
        pdf = export_word(path, output, config.word_timeout if config else 180)
        # Derived Word files are processed locally, regardless of the online PDF
        # extractor setting. MinerU authorization does not cover these uploads.
        rendered = parse_pdf(pdf, source, output/'layout', tight=True)
        if os.getenv('AITOWN_PORTABLE_HOME') or os.getenv('AITOWN_SOFFICE'):
            rendered.notes.append('使用 LibreOffice 本地转换；字体、公式与分页可能和 Microsoft Word 不同，请对照原件核对')
    except (OSError, ValueError, TimeoutError) as exc:
        if original is None:
            raise
        original.notes.append(str(exc)+'；已保留 DOCX 文字、公式与附图，可打开原件核对')
        original.status = 'partial'
        return original
    if original is None:
        rendered.notes.append('本地将原 DOC 转 PDF 后定位裁剪')
        rendered.rendered_pages=rendered.pages
        for question in rendered.questions:
            question.rendered_text=question.text
    text = '\n'.join(p.text for p in (original.pages if original else rendered.pages))
    if rendered.category != 'answer' and re.search(
            r'(?m)^\s*(?:证明思想\s*[:：]|/\*\s*解题分析|[（(](?:真|假)[)）])',text):
        result = original or rendered
        result.rendered_pages = rendered.pages if original else []
        result.status = 'needs_review'
        result.notes.append('Word 原件含已填答案或题中解题批注；题答边界尚未审校，暂不进入检索和截图展示；转换 PDF 与原件保留')
        return result
    if original:
        original.notes.extend(rendered.notes)
    return attach_word_layout(original, rendered) if original else rendered
