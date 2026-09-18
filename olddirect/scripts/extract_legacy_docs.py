"""Extract legacy OLE Word documents to UTF-8 text sidecars with Apache Tika."""
from __future__ import annotations
import json, subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];RESOURCE=ROOT/'resource';OUT=ROOT/'data'/'word_text';TIKA=ROOT/'data'/'tools'/'tika-app-2.9.2.jar'
TESS=Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe');TESSDATA=ROOT/'data'/'tessdata'
def embedded_ocr(path):
    texts=[]
    with tempfile.TemporaryDirectory(prefix='mathonline-doc-') as raw:
        folder=Path(raw);subprocess.run(['java','-jar',str(TIKA),'-z',f'--extract-dir={folder}',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        for image in sorted(folder.rglob('*')):
            if not image.is_file() or image.stat().st_size<5000:continue
            result=subprocess.run([str(TESS),str(image),'stdout','--tessdata-dir',str(TESSDATA),'-l','chi_sim','--psm','6','quiet'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if result.returncode==0:
                text=result.stdout.decode('utf-8','replace').strip()
                if text:texts.append(text)
    return '\n'.join(texts)
def main():
    if not TIKA.exists():raise SystemExit(f'Missing {TIKA}')
    files=sorted(p for p in RESOURCE.rglob('*.doc') if '\u6708\u8003' not in str(p));written=failed=0
    for path in files:
        target=OUT/(str(path.relative_to(RESOURCE))+'.txt');result=subprocess.run(['java','-jar',str(TIKA),'-t',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode:
            failed+=1;print(f'FAILED: {path.name}: {result.stderr.decode("utf-8","replace")[-300:]}');continue
        text=result.stdout.decode('utf-8','replace').strip()
        if len(text)<500:
            ocr=embedded_ocr(path)
            if ocr:text=f'{text}\n{ocr}'
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8');written+=1;print(f'DOC {written}/{len(files)}: {path.name} ({len(text)} chars)',flush=True)
    report={'candidates':len(files),'written':written,'failed':failed};(ROOT/'data'/'legacy_doc_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
if __name__=='__main__':main()
