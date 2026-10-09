"""Convert copies using a private LibreOffice profile; never touch originals."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
import fitz

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data/image_bank/runs/20261008-full-v2'
EXE=Path('D:/CodexTools/mathonline-libreoffice-26.2.6/unpacked/program/soffice.com')
PROFILE=Path('D:/CodexTools/mathonline-libreoffice-26.2.6/conversion-profile')

if __name__=='__main__':
    items=json.loads((ROOT/'data/image_bank/inventory.json').read_text('utf-8'))['files'];results=[]
    for item in [i for i in items if i['format']!='pdf']:
        target=RUN/'converted'/f"{item['id']}.pdf"
        result=dict(id=item['id'],original_path=item['path'],original_sha256=item['sha256'],converter='LibreOffice 26.2.6')
        try:
            if not target.exists():
                folder=RUN/'conversion_inputs'/item['id'];folder.mkdir(parents=True,exist_ok=True)
                source=folder/(item['id']+'.'+item['format'])
                if not source.exists():shutil.copy2(ROOT/item['path'],source)
                proc=subprocess.run([str(EXE),'-env:UserInstallation='+PROFILE.as_uri(),'--headless','--nologo','--nodefault','--norestore',
                    '--convert-to','pdf:writer_pdf_Export','--outdir',str(target.parent),str(source)],capture_output=True,timeout=150)
                log=proc.stdout.decode('utf-8','replace')+proc.stderr.decode('utf-8','replace')
                (RUN/'logs'/f"{item['id']}-conversion.txt").write_text(log,encoding='utf-8')
                if proc.returncode or not target.exists():raise RuntimeError(log[-1000:])
            with fitz.open(target) as pdf:
                result['pages']=len(pdf)
                for p in pdf:p.get_pixmap(matrix=fitz.Matrix(.3,.3))
            result['pdf_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            target.with_suffix('.pdf.sha256').write_text(item['sha256'],encoding='utf-8')
            result['status']='converted'
        except Exception as exc:result['status']='error';result['error']=str(exc)
        results.append(result)
        (RUN/'word_conversion.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print('CONVERTED',len(results),'/54',result['status'],item['name'],result.get('pages'),flush=True)
