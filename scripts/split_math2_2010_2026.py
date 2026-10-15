"""Scoped math-II source preparation and reproducible crop export."""
from pathlib import Path
import json,sys,hashlib,re,time,argparse
from concurrent.futures import ThreadPoolExecutor
import fitz,numpy as np
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data/math2_2010_2026'
BASE=ROOT/'devided-math/考研数学/考研数学 (二) 真题及答案解析（1987-2026）'
INV=json.loads((ROOT/'tmp/math2/inventory.json').read_text('utf8'))
SPECS=[(2010,4,346,359,'combined'),(2011,4,360,373,'combined'),(2012,4,374,384,'combined'),(2013,4,385,397,'combined'),(2014,4,398,401,'questions'),(2014,6,1,13,'combined'),(2015,7,1,15,'combined'),(2016,8,1,14,'combined'),(2017,10,1,3,'questions'),(2017,9,1,10,'combined'),(2018,12,1,3,'questions'),(2018,11,1,10,'combined'),(2019,13,1,4,'questions'),(2019,14,1,9,'combined'),(2020,15,1,5,'questions'),(2020,16,1,12,'combined'),(2021,17,1,9,'combined'),(2022,18,1,12,'combined'),(2023,19,1,13,'combined'),(2024,21,1,2,'questions'),(2024,20,1,6,'combined'),(2025,0,1,21,'combined'),(2026,23,1,5,'questions'),(2026,22,1,2,'answers') ]
def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def prepare():
    import build_image_bank_v2 as engine
    from image_bank_core import native_lines
    engine.RUN=RUN
    sources=[];jobs=[]
    for year,i,a,b,mode in SPECS:
        path=ROOT/INV[i]['path'];sid=f'{year}-d{i}'
        target=RUN/'sources'/f'{sid}.pdf';target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            with fitz.open(path) as src,fitz.open() as dst:
                dst.insert_pdf(src,from_page=a-1,to_page=b-1);dst.save(target)
        item={'id':sid,'name':f'{year}考研数学二'+('参考答案' if mode=='answers' else '真题' if mode=='questions' else '真题及解析')+'.pdf','path':target.relative_to(ROOT).as_posix(),'format':'pdf','pages':b-a+1,'year':year,'mode':mode,'original_path':path.relative_to(ROOT).as_posix(),'original_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'page_offset':a-1}
        sources.append(item)
        with fitz.open(target) as d:
            for pn,p in enumerate(d,1):
                cache=RUN/'ocr_coordinates'/sid/f'p{pn}.json'
                ls=native_lines(p);text=''.join(x['text'] for x in ls)
                if len(text)<80 or not re.search('[\u4e00-\u9fff]',text):
                    if not cache.exists():jobs.append((item,pn))
    write(RUN/'sources.json',sources)
    print('Sources',len(sources),'OCR pages',len(jobs),flush=True)
    if jobs:
        from rapidocr_onnxruntime import RapidOCR
        ocr=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
        for n,(item,pn) in enumerate(jobs,1):
            with fitz.open(ROOT/item['path']) as d:
                pix=d[pn-1].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
                arr=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
                result,_=ocr(arr)
            lines=[]
            for box,text,conf in result or []:
                xs=[p[0] for p in box];ys=[p[1] for p in box]
                lines.append({'text':text,'bbox':[min(xs)/pix.width,min(ys)/pix.height,max(xs)/pix.width,max(ys)/pix.height],'method':'ocr','confidence':float(conf)})
            write(RUN/'ocr_coordinates'/item['id']/f'p{pn}.json',{'lines':lines,'engine':'rapidocr-original-pixels'})
            print('OCR',n,'/',len(jobs),item['id'],pn,flush=True)
    for item in sources:
        engine.detect(item)
    summarize()
def summarize():
    import build_image_bank_v2 as engine
    for p in sorted((RUN/f'detections-v{engine.VERSION}').glob('*.json')):
        d=json.loads(p.read_text('utf8'));qs=d['questions']
        print(d['item']['id'],[(q['number'],q['record_type'],q['start']['page'],round(q['start']['bbox'][1],3),len(q['regions'])) for q in qs],flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--summary',action='store_true');args=parser.parse_args()
    summarize() if args.summary else prepare()
