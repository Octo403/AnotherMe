"""Complete local originals without reparsing reviewed PDF questions or answers."""
from pathlib import Path

from .archives import ARCHIVE_SUFFIXES, expand_archive
from .prepare import parse_file
from .repository import BankRepository, encode


def complete_previews(config, course, *, source_id=None, progress=lambda value:None):
    changed, failures, archives = [], [], []
    with BankRepository(config.database) as repo:
        pending = [s['id'] for s in repo.sources(course) if not s['remote_removed']
                   and (source_id is None or s['id']==source_id)]
        for sid in pending:
            source = repo.source(sid)
            path = Path(source['local_path']) if source['local_path'] else None
            try:
                if (source_id==sid and source.get('raw_deleted_at') and (not path or not path.is_file())
                        and (source['member'] or source['path']).lower().endswith(('.doc','.docx'))):
                    from .archives import restore_source
                    from .sources import GithubSource
                    remote=GithubSource(config)
                    try:
                        path=restore_source(repo,source,config,remote)
                    finally:
                        remote.close()
                if not path or not path.is_file() or path.name.startswith('~$'):
                    continue
                suffix = path.suffix.lower()
                if suffix in ARCHIVE_SUFFIXES:
                    if source['status']=='expanded':
                        continue
                    children = expand_archive(repo,source,path,config)
                    pending.extend(child for child in children if child not in pending)
                    repo.set_source(sid,status='expanded',error=None,notes=encode([f'已解包 {len(children)} 个成员']))
                    archives.append({'source_id':sid,'members':len(children)})
                elif suffix in ('.doc','.docx'):
                    parsed = parse_file(path,source,config.root/'processed'/str(sid),config)
                    if suffix=='.docx' and not parsed.rendered_pages:
                        raise ValueError(parsed.notes[-1])
                    if parsed.status=='needs_review':
                        repo.set_source(sid,status='needs_review',error=None,notes=encode(parsed.notes))
                        changed.append({'source_id':sid,'questions':0,'with_images':0,'withheld_for_review':True})
                        progress({'source_id':sid,'status':'needs_review'})
                        continue
                    existing = repo.conn.execute('SELECT count(*) FROM questions WHERE source_id=?',(sid,)).fetchone()[0]
                    if existing and source['status'] in ('ready','partial'):
                        repo.augment_word(sid,parsed)
                    elif not existing:
                        repo.replace_parsed(sid,parsed)
                    changed.append({'source_id':sid,'questions':len(parsed.questions),
                        'with_images':sum(bool(q.images) for q in parsed.questions)})
                elif source['status']=='downloaded':
                    # New archive members enter the normal parser; existing reviewed
                    # PDFs are deliberately untouched by this additive operation.
                    parsed = parse_file(path,source,config.root/'processed'/str(sid),config)
                    repo.replace_parsed(sid,parsed)
                    changed.append({'source_id':sid,'questions':len(parsed.questions),
                        'with_images':sum(bool(q.images) for q in parsed.questions)})
                else:
                    continue
                progress({'source_id':sid,'status':'completed'})
            except Exception as exc:
                repo.set_source(sid,error=f'{type(exc).__name__}: {exc}'[:1000])
                failures.append({'source_id':sid,'error':f'{type(exc).__name__}: {exc}'})
                progress({'source_id':sid,'status':'failed','error':str(exc)})
        from .layout import complete_layout_metadata
        layout_updated=complete_layout_metadata(repo,config,course)
        coverage = repo.coverage(course)
    from .cleanup import cleanup_raw
    cleanup=cleanup_raw(config,course,keep_originals=True)
    with BankRepository(config.database,readonly=True) as repo:
        coverage=repo.coverage(course)
    return {'completed':changed,'archives':archives,'failed':failures,'coverage':coverage,
            'layout_metadata_updated':layout_updated,'cleanup':cleanup}
