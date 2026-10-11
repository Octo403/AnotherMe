"""Keep script placement when comparing flattened PDF mathematical text."""
from collections import Counter
import json
from pathlib import Path


def layout_traits(document, question):
    ordered=[line.strip() for line in (question.rendered_text or question.text).splitlines() if line.strip()]
    wanted=Counter(ordered)
    started=False
    styles=[]
    vectors=[]
    raster=False
    ordinals=list(dict.fromkeys(r.page for r in question.image_regions)) or question.pages
    for ordinal in ordinals:
        if not 1<=ordinal<=len(document):
            return ''
        page=document[ordinal-1]
        selected_boxes=[]
        all_boxes=[]
        for block in page.get_text('dict',sort=True)['blocks']:
            for line in block.get('lines',[]):
                text=''.join(s['text'] for s in line['spans']).strip()
                if not text:
                    continue
                all_boxes.append(line['bbox'])
                if not started:
                    started=bool(ordered and text==ordered[0])
                    if not started:
                        continue
                if not wanted[text]:
                    continue
                wanted[text]-=1
                selected_boxes.append(line['bbox'])
                spans=[s for s in line['spans'] if s['text'].strip()]
                size=max((s['size'] for s in spans),default=1) or 1
                baseline=max((s['origin'][1] for s in spans),default=0)
                # Relative size/baseline records superscripts and subscripts, while
                # avoiding harmless 0.02pt export shifts and font subset names.
                styles.append([(round(s['size']/size,1),round((s['origin'][1]-baseline)/size,1))
                    for s in spans for char in s['text'] if not char.isspace()])
        regions=[r.bbox for r in question.image_regions if r.page==ordinal and r.bbox]
        if not regions and selected_boxes:
            top=min(b[1] for b in selected_boxes)-3
            last=max(b[1] for b in selected_boxes)
            following=[b[1] for b in all_boxes if b[1]>last+1]
            end=min(following)-2 if following else page.rect.height-12
            regions=[[0,top,page.rect.width,end]]
        vector_page=[]
        for drawing in page.get_drawings():
            rect=drawing['rect']
            if regions and not any(rect.x0>=b[0] and rect.x1<=b[2] and rect.y0>=b[1] and rect.y1<=b[3] for b in regions):
                continue
            def coordinates(value):
                if hasattr(value,'x'):
                    return [round(value.x),round(value.y)]
                if hasattr(value,'x0'):
                    return [round(v) for v in value]
                return value
            vector_page.append([drawing['type'],[[coordinates(v) for v in item] for item in drawing['items']],
                                drawing.get('color'),drawing.get('fill')])
        vectors.append(vector_page)
        for image in page.get_image_info():
            x0,y0,x1,y1=image['bbox']
            if not regions or any(x0<b[2] and x1>b[0] and y0<b[3] and y1>b[1] for b in regions):
                raster=True
    if any(wanted.values()):
        return ''
    return json.dumps({'styles':styles,'vectors':vectors,'raster':raster},separators=(',',':'))


def complete_layout_metadata(repo,config,course,*,refresh=False):
    """Backfill existing PDF imports without changing text, IDs or reviewed crops."""
    import pymupdf
    from .models import Question
    changed=0
    for source in repo.sources(course):
        if source['remote_removed'] or source['status'] not in ('ready','partial'):
            continue
        condition='' if refresh else " AND (dedup_layout='' OR dedup_layout NOT LIKE '%\"vectors\":%')"
        rows=repo.conn.execute('SELECT * FROM questions WHERE source_id=?'+condition,(source['id'],)).fetchall()
        if not rows or not source['local_path']:
            continue
        path=Path(source['local_path'])
        if path.suffix.lower() in ('.doc','.docx'):
            path=config.root/'processed'/str(source['id'])/'document.pdf'
        if path.suffix.lower()!='.pdf' or not path.is_file():
            continue
        with pymupdf.open(path) as pdf,repo.conn:
            for row in rows:
                data=repo.question_dict(row)
                value=layout_traits(pdf,Question.model_validate(data))
                if value:
                    repo.conn.execute('UPDATE questions SET dedup_layout=? WHERE id=?',(value,row['id']))
                    changed+=1
    return changed


def raster_content(path,question):
    """Decoded image bytes, no digest or approximate image similarity."""
    import pymupdf
    content=[]
    ordinals=list(dict.fromkeys(r['page'] for r in question['image_regions'])) or question['pages']
    with pymupdf.open(path) as document:
        for ordinal in ordinals:
            regions=[r['bbox'] for r in question['image_regions'] if r['page']==ordinal and r.get('bbox')]
            for block in document[ordinal-1].get_text('dict')['blocks']:
                if block['type']!=1:
                    continue
                x0,y0,x1,y1=block['bbox']
                if regions and not any(x0<b[2] and x1>b[0] and y0<b[3] and y1>b[1] for b in regions):
                    continue
                pixmap=pymupdf.Pixmap(block['image'])
                content.append((pixmap.width,pixmap.height,pixmap.n,pixmap.samples,block.get('mask')))
    return content
