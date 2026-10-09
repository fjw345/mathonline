"""Read the question-number column separately from mathematical page content."""
import csv
import argparse
import io
import json
import os
import re
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import fitz
from build_image_bank_v2 import ROOT,RUN,OLD,exam_identity,compact,is_footer


def work(item):
    did=item['id'];folder=RUN/'number_coordinates-v3'/did;folder.mkdir(parents=True,exist_ok=True)
    full=[]
    for pn in range(1,item['pages']+1):
        p=RUN/'ocr_coordinates'/did/f'p{pn}.json'
        if not p.exists():p=OLD/'ocr_coordinates'/did/f'p{pn}.json'
        if not p.exists():continue
        ls=json.loads(p.read_text('utf-8'))['lines'];full.append((pn,ls))
    xs=[l['bbox'][0] for _,ls in full for l in ls if len(l['text'])>12 and
        len(re.findall('[\u4e00-\u9fff]',l['text']))>5 and .015<l['bbox'][0]<.27 and not is_footer(l)]
    if not xs:return did,0
    bins=Counter(round(x/.015)*.015 for x in xs);mode=bins.most_common(1)[0][0]
    local=sorted(x for x in xs if abs(x-mode)<.011);margin=local[len(local)//2]
    count=0
    with fitz.open(ROOT/item.get('pdf_path',item['path'])) as pdf:
        for pn,ls in full:
            cache=folder/f'p{pn}.json'
            if cache.exists():continue
            p=pdf[pn-1];x0=max(0,margin-.009);x1=margin+.037
            clip=fitz.Rect(x0*p.rect.width,0,x1*p.rect.width,p.rect.height)
            pix=p.get_pixmap(matrix=fitz.Matrix(4,4),clip=clip,colorspace=fitz.csGRAY)
            png=folder/f'p{pn}.png';pix.save(png)
            outputs=[]
            for width,chars in [(.026,'0123456789().,'),(.032,'0123456789().,'),(.038,'0123456789().,'),(.037,'0123456789().,Il')]:
                pix=p.get_pixmap(matrix=fitz.Matrix(4,4),clip=fitz.Rect(x0*p.rect.width,0,(margin+width)*p.rect.width,p.rect.height),colorspace=fitz.csGRAY)
                png=folder/f'p{pn}-w{width}.png';pix.save(png)
                cmd=[r'C:\Program Files\Tesseract-OCR\tesseract.exe',str(png),'stdout','-l','eng','--psm','6',
                     '-c','tessedit_char_whitelist='+chars,'-c','tessedit_create_tsv=1']
                proc=subprocess.run(cmd,capture_output=True,timeout=60,env=dict(os.environ,OMP_THREAD_LIMIT='1'))
                outputs.extend(csv.DictReader(io.StringIO(proc.stdout.decode('utf-8','replace')),delimiter='\t'))
            words=[]
            for w in outputs:
                t=w.get('text','').strip()
                if not t:continue
                t=t.replace('I','1').replace('l','1')
                m=re.fullmatch(r'\(?(\d{1,2})[).,]?',t)
                if not m or not 1<=int(m.group(1))<=23:continue
                bx=x0+int(w['left'])/(p.rect.width*4)
                if bx>margin+.017:continue
                y=int(w['top'])/pix.height;h=int(w['height'])/pix.height
                if not .025<y<.95 or h<.003 or h>.055:continue
                nearby=[l for l in ls if abs(l['bbox'][1]-y)<.015 and l['bbox'][0]<margin+.016]
                if any(re.match(r'^[<(（《]?\s*[ABCDO][).,、）]',l['text']) for l in nearby):continue
                label='('+str(int(m.group(1)))+')'
                if any(l['text']==label and abs(l['bbox'][1]-y)<.01 for l in words):continue
                words.append(dict(text=label,bbox=[bx,y,bx+int(w['width'])/(p.rect.width*4),y+h],
                                  method='digit_ocr',confidence=float(w['conf']),number_candidate=True))
            cache.write_text(json.dumps(dict(margin=margin,lines=words),ensure_ascii=False),encoding='utf-8');count+=len(words)
    return did,count


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--word',action='store_true');args=parser.parse_args()
    items=json.loads((OLD/'inventory.json').read_text('utf-8'))['files']
    if args.word:
        items=[i for i in items if i['format']!='pdf' and (RUN/'converted'/f"{i['id']}.pdf").exists() and (RUN/'ocr_coordinates'/i['id']).exists()]
        for i in items:
            p=RUN/'converted'/f"{i['id']}.pdf"
            with fitz.open(p) as pdf:i['pages']=len(pdf)
            i['pdf_path']=p.relative_to(ROOT).as_posix()
    else:items=[i for i in items if i['format']=='pdf' and exam_identity(i)['category']=='考研' and (OLD/'ocr_coordinates'/i['id']).exists()]
    with ProcessPoolExecutor(max_workers=3) as pool:
        for n,(did,count) in enumerate(pool.map(work,items),1):print('DIGITS',n,'/',len(items),did,count,flush=True)
