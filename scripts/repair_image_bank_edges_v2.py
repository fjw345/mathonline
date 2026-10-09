"""Expand automatic regions touching ink. Preserve initial PNGs and manual stems."""
import hashlib
import json
import shutil
import math
from collections import OrderedDict
from pathlib import Path
import fitz
from PIL import Image
from build_image_bank_v2 import ROOT,RUN,write


def edge_ink(im,top=True):
    im=im.convert('L');w,h=im.size
    if h<8 or w<12:return 0
    strip=im.crop((max(1,int(w*.025)),0 if top else h-2,min(w-1,int(w*.975)),min(h,6) if top else h))
    return sum(strip.histogram()[:150])


def main():
    path=RUN/'manifest.json';manifest=json.loads(path.read_text('utf-8'))
    if manifest.get('edge_repair_version')==1:
        print('Boundary repair already applied');return
    backup=RUN/'before_boundary_repair';backup.mkdir(exist_ok=True)
    if (backup/'manifest.json').exists() and (backup/'manifest.json').read_bytes()!=path.read_bytes():
        backup=backup/('pass-'+hashlib.sha256(path.read_bytes()).hexdigest()[:12]);backup.mkdir(exist_ok=True)
    if not (backup/'manifest.json').exists():shutil.copy2(path,backup/'manifest.json')
    records=manifest['questions']+manifest['answer_records'];pages={p['id']:p for p in manifest['pages']};docs={d['id']:d for d in manifest['documents']}
    pdfs={};changes={};changed=[];page_images=OrderedDict()
    def render(region):
        p=pages[region['page_id']];did=p['document_id']
        cachekey=(did,p['number'])
        if cachekey in page_images:
            page_images.move_to_end(cachekey);full=page_images[cachekey]
        else:
            if did not in pdfs:
                if len(pdfs)>8:
                    _,old=pdfs.popitem();old.close()
                pdfs[did]=fitz.open(ROOT/docs[did]['path'])
            page=pdfs[did][p['number']-1]
            pix=page.get_pixmap(matrix=fitz.Matrix(2.2,2.2),alpha=False,colorspace=fitz.csRGB)
            full=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);page_images[cachekey]=full
            if len(page_images)>12:page_images.popitem(last=False)
        b=region['bbox']
        return full.crop((math.floor(b[0]*full.width),math.floor(b[1]*full.height),math.ceil(b[2]*full.width),math.ceil(b[3]*full.height)))
    for n,q in enumerate(records,1):
        for r in q['regions']:
            if q.get('manual_source') and r['role']=='stem':continue
            key=(r['page_id'],tuple(r['bbox']))
            if key in changes:continue
            with Image.open(RUN/r['image']) as im:
                upper=edge_ink(im,True)>4;lower=edge_ink(im,False)>8
            if not upper and not lower:continue
            updated=dict(r,bbox=list(r['bbox']));before=list(r['bbox'])
            for _ in range(4):
                if upper:updated['bbox'][1]=max(.005,updated['bbox'][1]-.01)
                if lower:updated['bbox'][3]=min(.995,updated['bbox'][3]+.008)
                im=render(updated)
                upper=edge_ink(im,True)>4;lower=edge_ink(im,False)>8
                if not upper and not lower:break
            if updated['bbox']!=before:
                changes[key]=updated['bbox'];changed.append(dict(page_id=r['page_id'],old=before,new=updated['bbox'],edge_contact_remaining=upper or lower))
        if n%1000==0:print('EDGE CHECK',n,'/',len(records),len(changes),'regions',flush=True)
    # Preserve neighbouring crops. Ink contact alone cannot establish whether
    # an overlapping formula belongs to the preceding or following question.
    affected=0
    for q in records:
        roles=set()
        for r in q['regions']:
            if q.get('manual_source') and r['role']=='stem':continue
            key=(r['page_id'],tuple(r['bbox']))
            if key not in changes:continue
            r['bbox_before_edge_repair']=list(r['bbox']);r['bbox']=changes[key]
            digest=hashlib.sha256(json.dumps(r['bbox']).encode()).hexdigest()[:10]
            target=RUN/Path(r['image']).parent/(r['id']+'-edge-'+digest+'.png')
            render(r).save(target);r['image']=target.relative_to(RUN).as_posix();roles.add(r['role'])
        if not roles:continue
        affected+=1;q['edge_repair_applied']=True
        for role in roles:
            rs=[r for r in q['regions'] if r['role']==role]
            if len(rs)==1:q['files'][role]=rs[0]['image'];continue
            pieces=[]
            for r in rs:
                with Image.open(RUN/r['image']) as im:pieces.append(im.convert('RGB'))
            img=Image.new('RGB',(max(p.width for p in pieces),sum(p.height for p in pieces)+18*(len(pieces)-1)),'white');y=0
            for p in pieces:img.paste(p,(0,y));y+=p.height+18
            digest=hashlib.sha256(json.dumps([r['image'] for r in rs]).encode()).hexdigest()[:10]
            target=RUN/Path(rs[0]['image']).parent/(role+'-edge-'+digest+'.png');img.save(target);q['files'][role]=target.relative_to(RUN).as_posix()
    for pdf in pdfs.values():pdf.close()
    manifest['edge_repair_version']=1;write(path,manifest)
    updated={q['id']:q for q in records}
    for workspace in (RUN/'workspaces').iterdir():
        for name in ['sample.json','annotations.json']:
            file=workspace/name
            if not file.is_file():continue
            data=json.loads(file.read_text('utf-8'))
            if name=='annotations.json' and data['revision']!=0:raise RuntimeError('User has edited this workspace; preserving it for manual merge: '+str(workspace))
            b=backup/'workspaces'/workspace.name/name;b.parent.mkdir(parents=True,exist_ok=True)
            if not b.exists():shutil.copy2(file,b)
            data['questions']=[updated.get(q['id'],q) for q in data['questions']];write(file,data)
    write(RUN/'edge_repair_report.json',dict(automatic_regions=len(changes),affected_records=affected,changes=changed))
    print('REPAIRED',len(changes),'regions across',affected,'records',flush=True)


if __name__=='__main__':main()
