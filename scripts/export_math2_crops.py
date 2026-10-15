"""Export scoped Math II source-pixel crops with auditable coordinates."""
from pathlib import Path
import sys,json,re,hashlib,csv,copy,shutil,time
import fitz,numpy as np
from PIL import Image,ImageDraw,ImageFont
import build_image_bank_v2 as e
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/"data/math2_2010_2026"
BASE=ROOT/"devided-math/考研数学/考研数学 (二) 真题及答案解析（1987-2026）"
e.RUN=RUN
SOURCES={x["id"]:x for x in json.loads((RUN/"sources.json").read_text("utf8"))}
DETECT={k:json.loads((RUN/"detections-v8"/(k+".json")).read_text("utf8"))["questions"] for k in SOURCES}
def save(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf8")
def anchor(n,p,y,x=.12,top=None):
    return dict(number=str(n),page=p,bbox=[x,y,x+.035,y+.014],top=top)
def detected(sid,count,first=True):
    qs=DETECT[sid][:count] if first else DETECT[sid][-count:]
    return [dict(number=q["number"],page=q["start"]["page"],bbox=q["start"]["bbox"],top=None) for q in qs]
ANCHORS={k:detected(k,22 if SOURCES[k]["year"]>=2021 else 23) for k in SOURCES}
ANCHORS["2014-d6"]=[anchor(n,p,y,.151) for n,p,y in [(1,1,.173),(2,1,.340),(3,1,.544),(4,1,.711),(5,2,.402),(6,2,.624),(7,2,.773),(8,3,.439),(9,3,.694),(10,3,.735),(11,4,.291),(12,4,.661),(13,5,.495),(14,5,.772),(15,6,.124),(16,6,.328),(17,7,.272),(18,7,.606),(19,8,.735),(20,9,.699),(21,10,.513),(22,10,.828),(23,12,.328)]]
ANCHORS["2014-d4"].append(anchor(9,2,.534,.168))
ANCHORS["2019-d14"].extend([anchor(9,3,.668,.126),anchor(19,6,.549,.126)])
ANCHORS["2020-d16"]=[dict(number=q["number"],page=q["start"]["page"],bbox=q["start"]["bbox"],top=None) for q in DETECT["2020-d16"] if not(q["start"]["page"]==9 and int(q["number"])<10)]
ANCHORS["2020-d16"].append(anchor(3,1,.851,.061))
ANCHORS["2022-d18"]=[dict(number=q["number"],page=q["start"]["page"],bbox=q["start"]["bbox"],top=None) for q in DETECT["2022-d18"] if not(int(q["number"])<5 and q["start"]["page"]>=6)]
ANCHORS["2022-d18"].extend([anchor(2,2,.700,.06,.685),anchor(6,4,.263,.06,.251)])
ANCHORS["2024-d20"].extend([anchor(1,1,.194,.148,.185),anchor(2,1,.456,.148,.401),anchor(5,2,.545,.148)])
ANCHORS["2024-d20"]=[dict(a,top=.615) if a["number"]=="8" else a for a in ANCHORS["2024-d20"]]
ANCHORS["2025-d0"]=[dict(number=q["number"],page=q["start"]["page"],bbox=q["start"]["bbox"],top=None) for q in DETECT["2025-d0"] if q["record_type"]=="question"]
ANCHORS["2025-d0"].append(anchor(13,11,.774,.098))
ROLE_FIX={"2015-d7":{21:(12,.658)},"2018-d11":{11:(5,.166)},"2022-d18":{18:(10,.151),19:(10,.581),20:(10,.887),22:(12,.489)},"2025-d0":{18:(16,.679),22:(21,.354)}}
START_FIX={"2012-d4":{15:.584},"2015-d7":{17:.585,21:.548},"2022-d18":{19:.495}}
START_FIX["2016-xdf"]={15:.801,22:.262}
ROLE_FIX["2016-xdf"]={3:(1,.888),15:(6,.8655)}
START_FIX["2015-d7"][17]=.630
ROLE_FIX["2023-d19"]={22:(12,.741)}
START_FIX["2024-d21"]={3:.4788,4:.584}
START_FIX["2022-d18"][19]=.496
QUESTION_SOURCE={2010:"2010-d4",2011:"2011-d4",2012:"2012-d4",2013:"2013-d4",2014:"2014-d4",2015:"2015-d7",2017:"2017-d10",2018:"2018-d12",2019:"2019-d13",2020:"2020-d15",2021:"2021-d17",2022:"2022-d18",2023:"2023-d19",2024:"2024-d21",2025:"2025-d0",2026:"2026-d23"}
QUESTION_SOURCE[2016]="2016-xdf"
QUESTION_SOURCE=dict(sorted(QUESTION_SOURCE.items()))
ANSWER_SOURCE={**QUESTION_SOURCE,2014:"2014-d6",2017:"2017-d9",2018:"2018-d11",2019:"2019-d14",2020:"2020-d16",2024:"2024-d20",2026:"2026-d22"}
DOCS={};LINES={};PIX={};PANELS={};IMAGES={};POINTS={}
def setup(sid):
    if sid in DOCS:return
    item=SOURCES[sid];d=fitz.open(ROOT/item["path"]);DOCS[sid]=d
    panels=[]
    for pn,p in enumerate(d,1):
        ls,_=e.choose_lines(p,item,pn)
        ls=[l for l in ls if not e.is_footer(l) and not re.search(r"数学试题及解析.*页|Page.*of|数学.*试题及解析|考研数学.*试题|参考答案.*简写版",l["text"]) and l["bbox"][1]<.93]
        if sid=="2016-xdf":ls=[l for l in ls if l["bbox"][1]>.12 and l["bbox"][3]<.915]
        LINES[(sid,pn)]=ls
        lanes=[(.025,.495),(.495,.985)] if sid=="2024-d21" else [(.025,.985)]
        for li,(left,right) in enumerate(lanes):
            lane=[l for l in ls if left<=l["bbox"][0]<right]
            top=max(.012,min([l["bbox"][1] for l in lane] or [.03])-.008)
            if sid=="2023-d19" and pn>1:top=max(top,.084)
            if sid in ["2010-d4","2011-d4","2012-d4","2013-d4"]:top=max(top,.075)
            bottom=min(.928,max([l["bbox"][3] for l in lane] or [.92])+.009)
            # OCR may omit formula-only content; preserve page body below it.
            if (RUN/"ocr_coordinates"/sid/f"p{pn}.json").exists():bottom=.925
            panels.append(dict(page=pn,lane=li,left=left,right=right,top=top,bottom=bottom))
    PANELS[sid]=panels
def point(sid,a):
    cachekey=(sid,a["page"],tuple(a["bbox"]),a.get("top"))
    if cachekey in POINTS:return POINTS[cachekey]
    li=1 if sid=="2024-d21" and a["bbox"][0]>.495 else 0
    pn=a["page"];ls=LINES[(sid,pn)];p=DOCS[sid][pn-1]
    lane=[l for l in ls if (.495 if li else .025)<=l["bbox"][0]<(.985 if li or sid!="2024-d21" else .495)]
    if (sid,pn) not in IMAGES:
        imgs=[]
        for b in p.get_text("dict")["blocks"]:
            if b["type"]==1 and (b["bbox"][3]-b["bbox"][1])/p.rect.height<.16:
                imgs.append([b["bbox"][0]/p.rect.width,b["bbox"][1]/p.rect.height,b["bbox"][2]/p.rect.width,b["bbox"][3]/p.rect.height])
        IMAGES[(sid,pn)]=imgs
    imgs=IMAGES[(sid,pn)]
    y=a["top"] if a.get("top") is not None else e.safe_top(dict(text="",bbox=a["bbox"]),lane,imgs)+.004
    POINTS[cachekey]=(pn,li,max(.01,y))
    return POINTS[cachekey]
def range_regions(sid,start,end):
    out=[]
    for panel in PANELS[sid]:
        pn,li=panel["page"],panel["lane"];key=(pn,li)
        if key<start[:2] or key>end[:2]:continue
        y0=start[2] if key==start[:2] else panel["top"]
        y1=end[2] if key==end[:2] else panel["bottom"]
        if y1-y0>.004:out.append(dict(source_id=sid,page=pn,bbox=[panel["left"],y0,panel["right"],y1]))
    return out
def section_end(sid,start,end):
    for panel in PANELS[sid]:
        pn,li=panel["page"],panel["lane"]
        for l in LINES[(sid,pn)]:
            if not(panel["left"]<=l["bbox"][0]<panel["right"]):continue
            t=e.compact(l["text"])
            if re.match(r"^[一二三][、.．:]?(?:选择|填空|解答)|^(?:参考答案|答案及解析|答案与解析)",t):
                pos=(pn,li,l["bbox"][1]-.008)
                if start<pos<end:end=pos
    return end
def solutions(sid,start,end,n):
    if n in ROLE_FIX.get(sid,{}):
        pn,y=ROLE_FIX[sid][n];return(pn,0,y)
    found=[]
    for panel in PANELS[sid]:
        pn,li=panel["page"],panel["lane"]
        for l in LINES[(sid,pn)]:
            if l["bbox"][0]>.3:continue
            t=e.compact(l["text"]);t=re.sub(r"^\d{1,2}[.．、]?", "",t)
            if re.match(r"^(?:[【\[](?:答案|解析|详解|证明|分析)[】\])]|(?:解|证明|解析|答案|解答)[：:])",t):
                a=dict(number=str(n),page=pn,bbox=l["bbox"],top=None);pos=point(sid,a)
                if start<pos<end:found.append(pos)
    return min(found) if found else None
def segments(sid,series="first"):
    setup(sid)
    aa=detected(sid,23,False) if series=="second" else ANCHORS[sid]
    aa=sorted(aa,key=lambda a:point(sid,a))
    count=22 if SOURCES[sid]["year"]>=2021 else 23
    assert sorted(int(a["number"]) for a in aa)==list(range(1,count+1)),(sid,[a["number"] for a in aa])
    result={}
    starts=[point(sid,a) for a in aa]
    if series=="first":
        starts=[(a["page"],pos[1],START_FIX.get(sid,{}).get(int(a["number"]),pos[2])) for a,pos in zip(aa,starts)]
    for i,a in enumerate(aa):
        n=int(a["number"]);start=starts[i]
        last=PANELS[sid][-1]
        end=starts[i+1] if i+1<len(aa) else (last["page"],last["lane"],last["bottom"])
        if series=="first" and sid in ["2010-d4","2011-d4","2013-d4"] and i==len(aa)-1:
            second=point(sid,detected(sid,23,False)[0]);end=second
        end=section_end(sid,start,end)
        if series=="first" and sid in ["2010-d4","2013-d4"] and n==23:end=(3,0,.925)
        if sid=="2014-d4" and n==14:end=(3,0,.141)
        split=None if SOURCES[sid]["mode"]=="questions" or (series=="first" and sid in ["2010-d4","2011-d4","2013-d4"]) else solutions(sid,start,end,n)
        result[n]=dict(start=start,end=end,split=split,stem=range_regions(sid,start,split or end),answer=range_regions(sid,split,end) if split else [])
    return result
def render(r):
    sid,pn=r["source_id"],r["page"];key=(sid,pn)
    if key not in PIX:
        pix=DOCS[sid][pn-1].get_pixmap(matrix=fitz.Matrix(3,3),alpha=False)
        PIX[key]=Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
    full=PIX[key];w,h=full.size;x0,y0,x1,y1=r["bbox"]
    box=(int(x0*w),int(y0*h),int(x1*w),int(y1*h));im=full.crop(box)
    arr=np.asarray(im.convert("L"));mask=arr<210
    # Trim only outer blank area. No resynthesis and no erasing source ink.
    yy,xx=np.nonzero(mask)
    if not len(xx):return None
    if len(xx)<12:return None
    trim=(max(0,int(xx.min())-18),max(0,int(yy.min())-15),min(im.width,int(xx.max())+19),min(im.height,int(yy.max())+16))
    r["bbox"]=[(box[0]+trim[0])/w,(box[1]+trim[1])/h,(box[0]+trim[2])/w,(box[1]+trim[3])/h]
    return im.crop(trim)
def export():
    plans={};records=[];counts=[]
    for year,sid in QUESTION_SOURCE.items():
        stems=segments(sid);aid=ANSWER_SOURCE[year]
        answers=segments(aid,"second" if year in [2010,2011,2013] else "first")
        count=23 if year<2021 else 22
        for n in range(1,count+1):
            ar=answers[n]
            answer_only=year in [2010,2011,2013,2014,2026]
            rr=range_regions(aid,ar["start"],ar["end"]) if answer_only else ar["answer"]
            if not rr:
                # Some answer PDFs lack an explicit "解析" heading for a
                # question; retain the complete source-number interval rather
                # than silently emitting a question without an answer image.
                rr=range_regions(aid,ar["start"],ar["end"])
            records.append(dict(id=f"kaoyan-math2-{year}-{n:02}",year=year,number=n,kind="choice" if n<=(8 if year<2021 else 10) else "blank" if n<=(14 if year<2021 else 16) else "solution",stem=stems[n]["stem"],answer=rr,boundary_status="draft_visual_review_pending",answer_status="same_exam_number_source_linked",grading_status="not_configured",note="2024/2026 源答案较简略" if year in [2024,2026] else ""))
    save(RUN/"crop_plan.json",records)
    previous=RUN/"manifest.json"
    if previous.exists():
        old=json.loads(previous.read_text("utf8"));history=RUN/"export_history"/str(time.time_ns())
        for q in old["questions"]:
            for role in ["stem","answer"]:
                for r in q[role]:
                    path=(ROOT/r["image"]).resolve()
                    if not path.is_relative_to(BASE.resolve()):raise RuntimeError("Unexpected old output path")
                    if path.exists():
                        if hashlib.sha256(path.read_bytes()).hexdigest()!=r["image_sha256"]:raise RuntimeError(f"Preserve modified image: {path}")
                        target=history/path.relative_to(BASE.resolve());target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(path),str(target))
        save(history/"manifest.json",old)
    missing=[]
    for q in records:
        for role,folder in [("stem","题目"),("answer","答案")]:
            rendered=[]
            for r in q[role]:
                im=render(r)
                if im is not None:rendered.append((r,im))
            q[role]=[r for r,_ in rendered]
            if not rendered:missing.append([q["year"],q["number"],role])
            for j,(r,im) in enumerate(rendered):
                suffix="" if len(rendered)==1 else ["上","下"][j] if len(rendered)==2 else "上" if j==0 else "下" if j==len(rendered)-1 else "中"+str(j)
                path=BASE/str(q["year"])/folder/(str(q["number"])+suffix+".png")
                path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists() and "--replace-generated" not in sys.argv:raise RuntimeError(f"Refuse overwrite {path}")
                im.save(path);r.update(image=path.relative_to(ROOT).as_posix(),order=j+1,original_page=r["page"]+SOURCES[r["source_id"]]["page_offset"],original_path=SOURCES[r["source_id"]]["original_path"],original_sha256=SOURCES[r["source_id"]]["original_sha256"],image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),width=im.width,height=im.height)
        if q["number"]==1:print("EXPORT",q["year"],flush=True)
    save(RUN/"manifest.json",dict(schema_version=1,sources=list(SOURCES.values()),questions=records,excluded=[dict(year=2016,source_id="2016-d8",reason="本地 PDF 和同名 DOC 错标为2016，内容实际为2014；已采用网上新东方2016题解原页补齐")],missing_regions=missing))
    for year in range(2010,2027):
        qs=[q for q in records if q["year"]==year]
        counts.append(dict(year=year,expected=23 if year<2021 else 22,questions=len(qs),with_answer=sum(bool(q["answer"]) for q in qs),stem_pngs=sum(len(q["stem"]) for q in qs),answer_pngs=sum(len(q["answer"]) for q in qs)))
    save(RUN/"coverage.json",counts)
    save(RUN/"review_queue.json",[dict(id=q["id"],reason="逐题视觉边界和数学答案尚未全部核验") for q in records])
    print("COUNTS",counts,"MISSING",missing,flush=True)
def sheets():
    data=json.loads((RUN/"manifest.json").read_text("utf8"));font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",16)
    for year in QUESTION_SOURCE:
        for role in ["stem","answer"]:
            tiles=[]
            for q in data["questions"]:
                if q["year"]!=year:continue
                for r in q[role]:
                    im=Image.open(ROOT/r["image"]);im.thumbnail((570,760))
                    tile=Image.new("RGB",(590,800),"white");tile.paste(im,(10,30));ImageDraw.Draw(tile).text((10,5),f"{q['id']} {role} part {r['order']} p{r['original_page']}",font=font,fill="red");tiles.append(tile)
            for offset in range(0,len(tiles),8):
                batch=tiles[offset:offset+8];sheet=Image.new("RGB",(2360,800*((len(batch)+3)//4)),"#ddd")
                for j,t in enumerate(batch):sheet.paste(t,((j%4)*590,(j//4)*800))
                path=RUN/"review"/f"{year}-{role}-{offset//8}.jpg";path.parent.mkdir(exist_ok=True);sheet.save(path,quality=90)
if __name__=="__main__":
    sheets() if "--sheets" in sys.argv else export()
