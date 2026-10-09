"""Recover coordinates from scanned/garbled pages, retaining every page image."""
import csv
import argparse
import io
import json
import os
import subprocess
import unicodedata
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'data/image_bank/runs/20261008-full-v2'


def work(item):
    folder = RUN / 'ocr_coordinates' / item['id']
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / f"p{item['page']}.json"
    if not item.get('force') and cache.exists() and json.loads(cache.read_text('utf-8')).get('lines'):
        return dict(item, status='cached')
    try:
        with fitz.open(ROOT / item['path']) as doc:
            pix = doc[item['page'] - 1].get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=False)
            png = folder / f"p{item['page']}.png"
            if not png.exists():
                pix.save(png)
            env = dict(os.environ, OMP_THREAD_LIMIT='1')
            proc = subprocess.run([r'C:\Program Files\Tesseract-OCR\tesseract.exe', str(png), 'stdout',
                                   '--tessdata-dir', str(ROOT/'data/tessdata'), '-l', 'chi_sim',
                                   '--psm', '3', '-c', 'tessedit_create_tsv=1'], capture_output=True, timeout=150, env=env)
            if proc.returncode:
                raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[-500:])
            groups = defaultdict(list)
            for w in csv.DictReader(io.StringIO(proc.stdout.decode('utf-8', 'replace')), delimiter='\t', quoting=csv.QUOTE_NONE):
                if w.get('text', '').strip():
                    groups[(w['block_num'], w['par_num'], w['line_num'])].append(w)
            lines = []
            for ws in groups.values():
                box = [min(int(w['left']) for w in ws)/pix.width, min(int(w['top']) for w in ws)/pix.height,
                       max(int(w['left'])+int(w['width']) for w in ws)/pix.width,
                       max(int(w['top'])+int(w['height']) for w in ws)/pix.height]
                lines.append(dict(text=unicodedata.normalize('NFKC', ' '.join(w['text'] for w in ws)),
                                  bbox=box, method='ocr', confidence=sum(float(w['conf']) for w in ws)/len(ws)))
            if cache.exists():
                backup=cache.with_suffix('.before-tsv-fix.json' if item.get('force') else '.empty-first-pass.json')
                if not backup.exists():backup.write_bytes(cache.read_bytes())
            cache.write_text(json.dumps(dict(lines=lines, engine='tesseract-chi_sim-psm3'), ensure_ascii=False), encoding='utf-8')
        return dict(item, status='ok', lines=len(lines))
    except Exception as exc:
        return dict(item, status='error', error=str(exc))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--word',action='store_true');args=parser.parse_args()
    if args.word:
        queue=[]
        for item in json.loads((ROOT/'data/image_bank/inventory.json').read_text('utf-8'))['files']:
            source=RUN/'converted'/f"{item['id']}.pdf"
            if item['format']=='pdf' or not source.exists():continue
            with fitz.open(source) as pdf:
                for n,p in enumerate(pdf,1):
                    t=p.get_text()
                    if len(t)<80:queue.append(dict(id=item['id'],path=source.relative_to(ROOT).as_posix(),page=n))
        (RUN/'word_ocr_queue.json').write_text(json.dumps(queue,ensure_ascii=False),encoding='utf-8')
    else:queue = json.loads((RUN/'ocr_queue.json').read_text('utf-8'))
    report=RUN/('word_ocr_progress.json' if args.word else 'ocr_progress.json')
    results = []
    with ProcessPoolExecutor(max_workers=3) as pool:
        for result in pool.map(work, queue):
            results.append(result)
            if len(results)%10 == 0 or result['status']=='error':
                print('OCR',len(results),'/',len(queue),result['status'],flush=True)
                report.write_text(json.dumps(results,ensure_ascii=False),encoding='utf-8')
    report.write_text(json.dumps(results,ensure_ascii=False),encoding='utf-8')
