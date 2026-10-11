"""Run with the isolated MinerU 4 interpreter; local SDK only, no API key."""
import argparse
import json
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--tier', choices=('flash','basic','standard','advanced'), default='flash')
    args = parser.parse_args()
    from mineru.config import VlmConfig, config
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    # Force a local VLM connection even if the machine has a remote MinerU config.
    config.model.base_dir = str(Path('data/runtime/mineru-models').resolve())
    started = time.perf_counter()
    result = parse(args.source.resolve(), tier=args.tier, vlm_config=VlmConfig())
    args.output.mkdir(parents=True, exist_ok=True)
    result.save(FileBasedDataWriter(str(args.output.resolve())))
    report = {'parser':'mineru', 'tier':args.tier, 'seconds':time.perf_counter()-started,
              'pages':len(result.pages), 'characters':len(result.markdown())}
    (args.output/'extraction.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
