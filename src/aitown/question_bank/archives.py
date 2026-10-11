"""Bounded local ZIP/RAR/7z extraction with registered member provenance."""
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .models import Source
from .sources import hints

ARCHIVE_SUFFIXES = {'.zip', '.rar', '.7z'}


def validate_members(members, output, config):
    if len(members) > config.max_archive_members or sum(m['size'] for m in members) > config.max_archive_bytes:
        raise ValueError('压缩包超出成员数量或解压大小限制')
    targets = set()
    for member in members:
        name = PurePosixPath(member['name'].replace('\\','/'))
        target = output.joinpath(*name.parts).resolve()
        if (not name.parts or name.is_absolute() or '..' in name.parts or ':' in member['name']
                or not target.is_relative_to(output.resolve()) or member.get('link') or member['size'] < 0
                or member.get('encrypted') or any(re.search(r'[\x00-\x1f<>"|?*]',p)
                    or p.endswith((' ','.')) or re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',p,re.I)
                    for p in name.parts)):
            raise ValueError('压缩包成员路径、链接或加密内容不安全')
        key = str(target).casefold()
        if key in targets:
            raise ValueError('压缩包含重复或大小写冲突的成员路径')
        targets.add(key)
        member['target'] = target


def seven_zip_members(text):
    members = []
    for block in re.split(r'\r?\n\s*\r?\n',text.strip()):
        fields = dict(line.split(' = ',1) for line in block.splitlines() if ' = ' in line)
        if 'Path' not in fields:
            continue
        attributes = fields.get('Attributes','')
        members.append({'name':fields['Path'],'size':int(fields.get('Size','0')),
            'directory':fields.get('Folder')=='+', 'encrypted':fields.get('Encrypted')=='+',
            'link':any('link' in key.lower() or 'reparse' in key.lower() for key in fields)
                or bool(re.search(r'(?:^|\s)l[rwx-]{9}',attributes)) or 'L' in attributes})
    return members


def _copy_bounded(reader, writer, size):
    copied = 0
    while chunk := reader.read(min(1024*1024,size-copied+1)):
        copied += len(chunk)
        if copied > size:
            raise ValueError('压缩包实际解压大小超出声明或限制')
        writer.write(chunk)
    if copied != size:
        raise ValueError('压缩包成员不完整')


def _extract_7z(executable, path, member, writer, timeout):
    # Extract exactly one validated member to a byte stream. 7z never chooses
    # filesystem targets or creates links, even for a malicious archive.
    process = subprocess.Popen([executable,'x','-so','-spd','-y','-bd','--',str(path.resolve()),member['name']],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    timer = threading.Timer(timeout,process.kill)
    timer.start()
    try:
        _copy_bounded(process.stdout,writer,member['size'])
        if process.wait(timeout=5):
            raise ValueError('7z 解压未完成或校验失败')
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


@contextmanager
def archive_contents(path, output, config):
    archive = None
    executable = None
    try:
        if path.suffix.lower()=='.zip':
            archive = zipfile.ZipFile(path)
            members = [{'name':m.filename,'size':m.file_size,'directory':m.is_dir(),
                'link':(m.external_attr>>16)&0o170000==0o120000,'encrypted':bool(m.flag_bits&1),'info':m}
                for m in archive.infolist()]
        else:
            executable = shutil.which('7z') or shutil.which('7zz')
            if not executable:
                raise OSError('解压 RAR/7z 需要本机 7-Zip 命令行工具')
            listing = subprocess.run([executable,'l','-slt','-ba','-sccUTF-8','--',str(path.resolve())],
                stdin=subprocess.DEVNULL,capture_output=True,timeout=config.timeout,check=True)
            members = seven_zip_members(listing.stdout.decode('utf-8-sig'))
        # Reject the entire unsafe directory before writing even its first file.
        validate_members(members,output,config)
        yield archive,executable,members
    finally:
        if archive:
            archive.close()


def extract_member(path, name, output, config):
    """Extract one member from a fresh public archive without changing a bank."""
    with archive_contents(path,output,config) as (archive,executable,members):
        member=next((m for m in members if m['name'].replace('\\','/')==name.replace('\\','/') and not m['directory']),None)
        if member is None:
            raise ValueError('公开压缩包中找不到指定成员')
        target=member['target']
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('wb') as writer:
            if archive:
                with archive.open(member['info']) as reader:
                    _copy_bounded(reader,writer,member['size'])
            else:
                _extract_7z(executable,path,member,writer,config.timeout)
        return target


def expand_archive(repo, source, path, config, *, preserve_status=False):
    if source['member'].count('!/') >= 3:
        raise ValueError('嵌套压缩包超过 3 层')
    output = config.root/'raw'/str(source['id'])/'members'
    if source['repository'].startswith('user:'):
        output.parent.mkdir(parents=True,exist_ok=True)
        output=Path(tempfile.mkdtemp(prefix='members-',dir=output.parent))
    with archive_contents(path,output,config) as (archive,executable,members):
        ids = []
        prefix = source['member']+'!/' if source['member'] else ''
        for member in members:
            if member['directory']:
                continue
            target = member['target']
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as temporary:
                temporary_path = Path(temporary.name)
                try:
                    if archive:
                        with archive.open(member['info']) as reader:
                            _copy_bounded(reader,temporary,member['size'])
                    else:
                        _extract_7z(executable,path,member,temporary,config.timeout)
                except BaseException:
                    temporary.close()
                    temporary_path.unlink(missing_ok=True)
                    raise
            temporary_path.replace(target)
            item = Source(course_id=source['course_id'],repository=source['repository'],path=source['path'],
                member=prefix+member['name'],url=source['url'],size=member['size'],**hints(member['name']))
            with repo.conn:
                sid = repo.save_source(item)
            fields={'local_path':str(target),'raw_deleted_at':None}
            if not preserve_status:
                fields['status']='downloaded'
            repo.set_source(sid,**fields)
            ids.append(sid)
        with repo.conn:
            for row in repo.conn.execute("SELECT id,member FROM sources WHERE course_id=? AND repository=? AND path=? AND member<>''",
                    (source['course_id'],source['repository'],source['path'])).fetchall():
                relative = row['member'][len(prefix):] if row['member'].startswith(prefix) else None
                if relative is not None and '!/' not in relative and row['id'] not in ids:
                    repo.conn.execute('UPDATE sources SET remote_removed=1 WHERE id=?',(row['id'],))
        return ids


def restore_source(repo, source, config, remote, *, update=False):
    """Re-fetch a cleaned source/member without invalidating sibling questions."""
    current=Path(source['local_path']) if source['local_path'] else None
    if not source['member']:
        if source['repository']=='user:upload':
            if not current or not current.resolve().is_relative_to((config.root/'raw').resolve()) or not current.is_file():
                raise ValueError('上传原件已丢失，请重新上传同名文件并选择替换')
            return current
        if source['repository']=='user:url':
            from .supplements import download_url
            return download_url(repo,source,config,update=update)
        path=config.root/'raw'/str(source['id'])/PurePosixPath(source['path']).name
        remote.download(source,path,update=update)
        repo.set_source(source['id'],local_path=str(path),raw_deleted_at=None)
        return path
    if current and current.is_file() and not update and current.stat().st_size and (
            source['size'] is None or current.stat().st_size==source['size']):
        return current
    parent_member=source['member'].rsplit('!/',1)[0] if '!/' in source['member'] else ''
    parent=repo.conn.execute('SELECT * FROM sources WHERE course_id=? AND repository=? AND path=? AND member=?',
        (source['course_id'],source['repository'],source['path'],parent_member)).fetchone()
    if parent is None:
        raise ValueError('压缩包父来源不存在，无法恢复成员')
    parent=dict(parent)
    path=restore_source(repo,parent,config,remote,update=update)
    expand_archive(repo,parent,path,config,preserve_status=True)
    restored=repo.source(source['id'])
    if restored['remote_removed'] or not restored['local_path'] or not Path(restored['local_path']).is_file():
        raise ValueError('当前压缩包中找不到原成员')
    return Path(restored['local_path'])
