"""Standalone PaddleOCR experiment runner; run inside .venv-paddle."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import time
import traceback


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('PADDLE_PDX_MODEL_SOURCE','BOS')
    # Keep downloaded models inside the experiment workspace.
    os.environ.setdefault('PADDLE_PDX_CACHE_HOME',str(Path('data/models/paddle').resolve()))
    report={'provider':'PP-StructureV3','formula_model':'PP-FormulaNet_plus-M',
        'device':args.device,'results':[],'status':'running'}
    started=time.perf_counter()
    try:
        from paddleocr import PPStructureV3
        pipeline=PPStructureV3(device=args.device,use_doc_orientation_classify=False,
            use_doc_unwarping=False,use_textline_orientation=False,use_table_recognition=False,
            use_seal_recognition=False,use_chart_recognition=False,use_formula_recognition=True,
            formula_recognition_model_name='PP-FormulaNet_plus-M',enable_mkldnn=False,cpu_threads=4)
        report['initialization_seconds']=time.perf_counter()-started
        report['versions']={name:importlib.metadata.version(name) for name in ('paddleocr','paddlepaddle','paddlex')}
        for sample in json.loads(args.manifest.read_text(encoding='utf-8')):
            tick=time.perf_counter()
            outputs=list(pipeline.predict(sample['path']))
            assert len(outputs)==1,'Each sample is one original page region'
            result=outputs[0]
            result.save_to_json(str(args.output/(sample['key']+'.json')))
            result.save_to_markdown(str(args.output))
            raw=result.json
            data=raw.get('res',raw)
            blocks=data.get('parsing_res_list',[])
            item={**sample,'seconds':time.perf_counter()-tick,
                'text':'\n'.join(b.get('block_content','') for b in blocks),
                'blocks':[{'label':b.get('block_label'),'bbox':b.get('block_bbox'),
                    'text':b.get('block_content','')} for b in blocks],
                'formula_regions':len(data.get('formula_res_list',[]))}
            report['results'].append(item)
            (args.output/'paddle.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'sample':sample['key'],'seconds':item['seconds'],'blocks':len(blocks)},ensure_ascii=False),flush=True)
        report['status']='complete'
    except Exception:
        report['status']='failed';report['error']=traceback.format_exc()
        raise
    finally:
        report['total_seconds']=time.perf_counter()-started
        (args.output/'paddle.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
