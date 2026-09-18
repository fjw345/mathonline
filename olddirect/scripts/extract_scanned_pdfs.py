"""OCR image-only PDF pages into JSON sidecars without modifying source PDFs."""
from __future__ import annotations
import argparse, json, sqlite3, subprocess, tempfile
from pathlib import Path
import fitz

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'; DB=DATA/'problem_bank.sqlite3'; OCR_ROOT=DATA/'ocr_text'
TESS=Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
TESSDATA=DATA/'tessdata'

def sidecar(path): return OCR_ROOT/(str(path.relative_to(ROOT/'resource'))+'.json')
def ocr_page(page,tmp,page_no):
    pix=page.get_pixmap(matrix=fitz.Matrix(2.5,2.5),colorspace=fitz.csGRAY,alpha=False)
    image=tmp/f'page-{page_no:04d}.png';pix.save(image)
    command=[str(TESS),str(image),'stdout','--tessdata-dir',str(TESSDATA),'-l','chi_sim','--psm','6','quiet']
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8','replace'))
    return result.stdout.decode('utf-8','replace').strip()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);parser.add_argument('--force',action='store_true');parser.add_argument('--unparsed',action='store_true');args=parser.parse_args()
    if not TESS.exists() or not (TESSDATA/'chi_sim.traineddata').exists(): raise SystemExit('Tesseract or chi_sim.traineddata is missing')
    db=sqlite3.connect(DB)
    query="select path from problem_sources where included=1 and lower(filename) like '%.pdf' and text_page_count=0 order by path"
    if args.unparsed:query="select path from problem_sources s where included=1 and lower(filename) like '%.pdf' and not exists(select 1 from problem_source_links l where l.source_id=s.id) order by path"
    paths=[ROOT/row[0] for row in db.execute(query)];db.close()
    if args.limit: paths=paths[:args.limit]
    done=pages_done=0
    for path in paths:
        out=sidecar(path)
        if out.exists() and not args.force: continue
        doc=fitz.open(path);texts=[]
        with tempfile.TemporaryDirectory(prefix='mathonline-ocr-') as raw:
            tmp=Path(raw)
            for number,page in enumerate(doc,1): texts.append(ocr_page(page,tmp,number));pages_done+=1
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps({'source':str(path.relative_to(ROOT)).replace('\\','/'),'engine':'tesseract-chi_sim','replace_all':bool(args.unparsed),'pages':texts},ensure_ascii=False,indent=2),encoding='utf-8');done+=1
        print(f'OCR {done}/{len(paths)}: {path.name} ({len(texts)} pages)')
    print(json.dumps({'sources_written':done,'pages_ocrd':pages_done,'candidate_sources':len(paths)}))
if __name__=='__main__':main()
