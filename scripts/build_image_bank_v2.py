"""Resume-safe image extraction. Originals and previous batches are never changed.

Coordinates are drafts unless imported from the existing manual trial. Text is
only a location/search hint: all deliverable question/answer pixels are rendered
from source PDFs (or a separately preserved Word conversion).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import fitz
from PIL import Image
from image_bank_core import native_lines, kind_of, normalize

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'data/image_bank/runs/20261008-full-v2'
OLD = ROOT / 'data/image_bank'
VERSION = 8
REFRESH = False
PREVIOUS_RECORDS = {}
CN = '一二三四五六七八九十'
SECTION = re.compile(r'^([一二三四五六七八九十]+)[、.．:：]\s*(.*)')
ARABIC = re.compile(r'^\s*(?:[（(]\s*(\d{1,2})\s*[)）]|(\d{1,2})\s*[.．、:：](?!\d)|(\d{1,2})(?=\s*[【\[]\s*答案))\s*(.*)')
SOLUTION = re.compile(r'^(?:[【\[]\s*(答案|解析|解答|答|详解)\s*[】\]]|(?:解答|解析|答案|证明|解法\s*[一二三123]?|解|证|答)\s*[:：.．]|解\s+(?=\S))')
ANSWER_HEADING = re.compile(r'^(?:参考答案(?:简写版)?|参考解答|答案(?:及|与|和)?解析|答案|试题解答|参考答案与解析|参考答案及评分标准)$')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def compact(text):
    return re.sub(r'\s+', '', normalize(text))


def cn_number(s):
    if s.isdigit():
        return int(s)
    if s == '十':
        return 10
    if '十' in s:
        a, b = s.split('十')
        return (CN.index(a)+1 if a else 1)*10 + (CN.index(b)+1 if b else 0)
    return CN.index(s)+1 if s in CN else 0


def exam_identity(item):
    name, path = compact(item['name']), compact(item['path'])
    year = re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', name)
    academic = re.search(r'(20\d{2})[-—](20\d{2})[-—]([12])',name)
    ident = dict(category='', year=year[0] if len(set(year))==1 else None, subject=None,
                 group=None, session='regular', stage=None, academic_term=None, edition=None)
    if '考研' in path:
        ident['category'] = '考研'
        vol = re.search(r'(?:数学|数|卷)[(（]?([一二三四])',name)
        if not vol:
            vol = re.search(r'数学[(（]([一二三])',path)
        ident['subject'] = '数学'+(vol.group(1) if vol else '?')
        if name.startswith('26数'):
            ident['year']='2026'
        if '一、二、三' in name:
            ident['subject']=None
    elif '全国大学生' in path:
        ident['category']='全国大学生数学竞赛'
        ident['subject']='非数学' if '非数' in name else '数学'
        ed=re.search(r'(?<![-\d])(\d{1,2})届',name)
        if ed and not re.search(r'\d+-\d+届',name):
            ident['edition']=int(ed.group(1)); ident['year']=str(2008+int(ed.group(1)))
        group=re.search(r'(?:数学类?|非数类)([AB])',name)
        if group: ident['group']=group.group(1)
        ident['stage']='初赛'
    elif '北京市' in path:
        ident['category']='北京市大学生数学竞赛'; ident['subject']='非数学'
    elif '数学竞赛' in path:
        ident['category']='北京交通大学数学竞赛'; ident['subject']='非数学'
    else:
        ident['category']='北京交通大学课程考试'
        ident['subject']=('几何与代数B' if re.search('几何.*代数',name) else
                          '概率统计B' if '概率' in name else
                          '微积分B下' if '下' in name else '微积分B上')
        ident['stage']='月考一' if re.search(r'月考一|第一次月考',name) else '月考二' if re.search(r'月考二|第二次月考',name) else '期末'
        group=re.search(r'[(（]([AB甲乙])[)）]',name)
        if group: ident['group']=group.group(1)
    if academic:
        ident['academic_term']='-'.join(academic.groups());ident['year']=academic.group(1)
    return ident


def update_identity(ident, text):
    t=compact(text)
    value=dict(ident)
    # Only headings identify an exam. Dates within a problem do not.
    heading=bool(re.search(r'考试|考研|试题|试卷|竞赛|月考|期末',t))
    if heading and len(t)<130:
        years=re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)',t)
        if years: value['year']=years[0]
        term=re.search(r'(20\d{2})[-—](20\d{2})[-—]([12])',t)
        if term:value['academic_term']='-'.join(term.groups());value['year']=term.group(1)
        ed=re.search(r'第([一二三四五六七八九十\d]+)届',t)
        if ed:
            value['edition']=cn_number(ed.group(1))
            if value['category']=='全国大学生数学竞赛': value['year']=str(2008+value['edition'])
        value['session']='补赛二' if '补赛二' in t else '补赛' if '补赛' in t else 'regular'
        if re.search(r'第一次月考|月考一',t):value['stage']='月考一'
        elif re.search(r'第二次月考|月考二',t):value['stage']='月考二'
        elif '期末' in t:value['stage']='期末'
    if ident['category']=='考研' and len(t)<90:
        vol=re.search(r'(?:数学|试卷|卷)[(（]?([一二三四])[)）]?',t)
        if vol and (heading or len(t)<22):value['subject']='数学'+vol.group(1)
    if len(t)<100 and (heading or re.search(r'[(（](?:非)?数学[AB]类',t)):
        group=re.search(r'(?:非数学|数学)([AB])类|([甲乙AB])(?:组|卷)',t)
        if group:value['group']=next(g for g in group.groups() if g)
        if '甲、乙' in t or '甲乙' in t:value['group']='甲乙'
        if re.search('非数学|非数类',t):value['subject']='非数学'
        years=re.findall(r'20\d{2}',t)
        if years and value['category']=='全国大学生数学竞赛': value['year']=years[0]
    return value


def marker(line):
    if line.get('number_disabled'):return None
    if line.get('selected_question_number'):return line['selected_question_number'],line.get('selected_rest',''),line.get('marker_style','paren')
    t=normalize(line['text']);m=ARABIC.match(t)
    if m:
        n=int(next(g for g in m.groups()[:3] if g))
        if 1<=n<=60:return str(n),m.group(4),'paren' if m.group(1) else 'arabic'
    m=SECTION.match(compact(t).lstrip('-—·'))
    if m:
        return m.group(1),m.group(2),'chinese'
    return None


def section_kind(rest):
    t=compact(rest)
    if re.match(r'(?:单项)?选择|单选|填空|解答题|计算题|证明题|综合题|判断题',t):return kind_of(t)
    if re.match(r'[(（]?(?:本题|本大题).*每小题',t):return 'unknown'
    return None


def is_footer(line):
    t=compact(line['text']);x,y,x1,y1=line['bbox']
    return (bool(re.search(r'E-mail|微信|QQ:|Born.?to.?win|公众号|微博|考研数学于文涛',t,re.I)) or
            (y>.89 and bool(re.fullmatch(r'[-—·]*(?:第)?\d+(?:页)?(?:共\d+页)?[-—·]*',t))) or
            (y<.09 and bool(re.search(r'配套光盘|复习大全|学习帮手',t))))


def choose_lines(page,item,pn):
    native=native_lines(page)
    for line in native:
        m=SECTION.match(compact(line['text']))
        if m and not m.group(2):
            neighbors=[l for l in native if abs(l['bbox'][1]-line['bbox'][1])<.01 and
                       0<=l['bbox'][0]-line['bbox'][2]<.10 and section_kind(l['text'])]
            if neighbors:line['text']+=min(neighbors,key=lambda l:l['bbox'][0])['text']
    t=''.join(l['text'] for l in native);cn=len(re.findall('[\u4e00-\u9fff]',t))
    bad=len(t)<80 or (cn<5 and len(t)>50) or (cn<20 and sum(ord(c)<32 and c not in '\n\r\t' for c in t)>5)
    override_file=RUN/'page_detection_overrides.json'
    overrides=json.loads(override_file.read_text('utf-8')).get(item['id'],{}) if override_file.exists() else {}
    if bad or pn in overrides.get('force_ocr',[]):
        for folder in [RUN,OLD]:
            p=folder/'ocr_coordinates'/item['id']/f'p{pn}.json'
            if p.exists():
                lines=json.loads(p.read_text('utf-8'))['lines']
                if not lines:continue
                # Latin digits frequently survive broken CJK font maps. Use
                # those positions to recover OCR-missed question numbers.
                for l in native:
                    m=marker(l)
                    if m and m[2]=='paren' and not any(marker(o) and abs(o['bbox'][1]-l['bbox'][1])<.018 and abs(o['bbox'][0]-l['bbox'][0])<.05 for o in lines):
                        lines.append(dict(l,method='native_number',text='('+m[0]+') '+m[1]))
                return lines,'ocr'
        return native,'ocr_missing'
    return native,'text'


def lanes_for(lines):
    anchors=[l for l in lines if marker(l) and marker(l)[2]!='chinese' and marker(l)[1].strip()]
    a=sum(l['bbox'][0]<.25 for l in anchors)
    b=sum(.47<l['bbox'][0]<.69 for l in anchors)
    crossing=sum(l['bbox'][0]<.4 and l['bbox'][2]>.75 and len(l['text'])>16 for l in lines)
    if a>=2 and b>=2 and crossing<2:return [(.02,.495),(.495,.985)]
    return [(.025,.985)]


def safe_top(line,lines,images,previous=0):
    """Use complete inline formula bounds, including image-based Word equations."""
    if 'boundary_top' in line:return max(previous+.001,line['boundary_top'])
    x,y,x1,y1=line['bbox']
    nearby=[l['bbox'] for l in lines if l['bbox'][1]<y1-.004 and l['bbox'][3]>y+.004 and
            l['bbox'][1]>y-.06 and l['bbox'][3]-l['bbox'][1]<.13]
    nearby += [b for b in images if b[1]<y1 and b[3]>y and b[3]-b[1]<.16]
    # Braces may be represented by stacked short glyphs; follow their overlap
    # upward so all three rows of a system of equations remain in the crop.
    band=min([y]+[b[1] for b in nearby])
    for _ in range(5):
        linked=[l['bbox'][1] for l in lines if y-.065<l['bbox'][1]<band and l['bbox'][3]>band+.001 and l['bbox'][3]-l['bbox'][1]<.13]
        if not linked:break
        band=min(linked)
    top=band-.012
    # Extra top room without cutting preceding content: the exported previous
    # region also reaches this shared boundary, with a small protective overlap.
    return max(previous+.001,.012,top)


def answer_document(item):
    n=compact(item['name'])
    if '北交大历年期末真题' in item['path']:return False
    if n.startswith(('附录2','附录4')):return True
    if re.search(r'真题及|真题\+|真题.*含解析|试题及|试题参考|真题答案与',n):return False
    return bool(re.search('参考答案|真题解析|一、二、三答案',n))


def select_scan_numbers(pdf,item,ident):
    """Align independently read numbers in page order; retain the evidence."""
    number_dir=RUN/'number_coordinates-v3'/item['id']
    if not number_dir.exists() or ident['category']!='考研' or not ident.get('year') or int(ident['year'])<2003:return {}
    maximum=22 if int(ident['year'])>=2021 else 23
    candidates=[];page_lines={}
    for pn,p in enumerate(pdf,1):
        ls,method=choose_lines(p,item,pn);page_lines[pn]=ls
        if method!='ocr':continue
        numbers=number_dir/f'p{pn}.json'
        if not numbers.exists():continue
        data=json.loads(numbers.read_text('utf-8'));margin=data['margin']
        source=[]
        for l in ls:
            m=marker(l);t=compact(l['text'])
            if not m:
                extra=re.match(r'^(?:[《〈<]\s*)?(\d{1,2})(?:[》〉>]|\s+(?=[\u4e00-\u9fff])|(?=[设求已如若]))(.*)',l['text'])
                if extra:m=(extra.group(1),extra.group(2),'paren')
            if m and m[0].isdigit() and 1<=int(m[0])<=maximum and abs(l['bbox'][0]-margin)<.05:
                source.append(dict(l,n=int(m[0]),rest=m[1],score=5.0+(2 if len(m[1])>2 else 0)))
        for l in data['lines']:
            n=int(re.search(r'\d+',l['text']).group())
            if n>maximum:continue
            if any(x['n']==n and abs(x['bbox'][1]-l['bbox'][1])<.023 for x in source):
                best=min((x for x in source if x['n']==n),key=lambda x:abs(x['bbox'][1]-l['bbox'][1]));best['score']+=2
                continue
            source.append(dict(l,n=n,rest='',score=3+max(0,l['confidence'])/100))
        overrides=RUN/'visual_number_overrides.json'
        if overrides.exists():
            for entry in json.loads(overrides.read_text('utf-8')).get(item['id'],[]):
                if entry['page']==pn:
                    source=[c for c in source if c['n']!=entry['number']]
                    source.append(dict(text='('+str(entry['number'])+')',bbox=entry['bbox'],method='visual_number',n=entry['number'],rest='',score=20))
        for c in source:
            y=c['bbox'][1];near=[l for l in ls if y-.03<l['bbox'][1]<y+.012 and abs(l['bbox'][0]-margin)<.085]
            text=''.join(compact(l['text']) for l in near)
            if not answer_document(item) and re.search(r'【解析|\[解析|【答案|\[答案|故选|解:|由\(\d',text):c['score']-=6
            if re.search(r'设|已知|下列|求|若|曲线|函数|随机变量|本题满分',text):c['score']+=2
            if re.search(r'选择题.*小题|考试时间|全国.*考试|填空题.*小题',text):c['score']-=8
            if re.match(r'^[<(（《]?\s*[ABCDO][).,、）]',c.get('rest','')):c['score']-=5
            c['score']-=abs(c['bbox'][0]-margin)*70
            c['page']=pn;c['position']=pn+y;candidates.append(c)
    if len(list(number_dir.glob('p*.json')))<len(pdf)*.6:return {}
    candidates.sort(key=lambda c:c['position'])
    best=[]
    for k,c in enumerate(candidates):
        gain=c['score']+4
        value=gain-1.5*(c['n']-1);previous=None
        for j,a in enumerate(candidates[:k]):
            if a['n']<c['n'] and c['position']-a['position']>.008:
                score=best[j][0]+gain-1.5*(c['n']-a['n']-1)
                if score>value:value=score;previous=j
        best.append((value,previous))
    if not best:return {}
    index=max(range(len(best)),key=lambda i:best[i][0]-1.5*(maximum-candidates[i]['n']))
    selected=[]
    while index is not None:
        selected.append(candidates[index]);index=best[index][1]
    selected.reverse();write(RUN/'number_alignment'/f"{item['id']}.json",dict(expected=maximum,selected=selected))
    bypage=defaultdict(list)
    brief_inline=(not answer_document(item) and sum('解析' in compact(l['text'])[:10] for ls in page_lines.values() for l in ls)<2)
    for ci,c in enumerate(selected):
        box=list(c['bbox'])
        if brief_inline and c['method']!='visual_number':
            ls=page_lines[c['page']]
            previous_answers=[l for l in ls if l['bbox'][1]<box[1] and re.match(r'^[【\[]答案',compact(l['text']))]
            if previous_answers:
                answer=max(previous_answers,key=lambda l:l['bbox'][1])
                previous_number=selected[ci-1]['bbox'][1] if ci and selected[ci-1]['page']==c['page'] else 0
                body=[l for l in ls if answer['bbox'][1]>previous_number and answer['bbox'][3]+.012<l['bbox'][1]<box[1]-.015 and len(compact(l['text']))>4 and not is_footer(l)
                      and not (marker(l) and marker(l)[2]=='chinese')]
                if body:
                    first=min(body,key=lambda l:l['bbox'][1]);box[1]=min(box[1],first['bbox'][1]);box[3]=max(box[1]+.014,first['bbox'][3])
        bypage[c['page']].append(dict(text='('+str(c['n'])+') '+c['rest'],bbox=c['bbox'],method=c['method'],
                                     selected_question_number=str(c['n']),selected_rest=c['rest'],alignment_score=c['score']))
        bypage[c['page']][-1]['number_bbox']=c['bbox'];bypage[c['page']][-1]['bbox']=box
    return bypage


def detect(item):
    cache=RUN/f'detections-v{VERSION}'/f"{item['id']}.json"
    if cache.exists():
        dependencies=[p for folder in ['ocr_coordinates','number_coordinates-v3'] for p in (RUN/folder/item['id']).glob('p*.json')]
        if not REFRESH and (not dependencies or max(p.stat().st_mtime for p in dependencies)<=cache.stat().st_mtime):
            return json.loads(cache.read_text('utf-8'))
        backup=RUN/'cache_history'/(item['id']+'-'+str(time.time_ns())+'.json')
        backup.parent.mkdir(exist_ok=True);backup.write_bytes(cache.read_bytes())
    source=ROOT/item['path'] if item['format']=='pdf' else RUN/'converted'/f"{item['id']}.pdf"
    if not source.exists():return dict(item=item,status='conversion_pending',pages=[],questions=[],issues=[{'type':'missing_conversion'}])
    ident=exam_identity(item);current=None;kind='unknown';section='';chinese_parent=False;segment=0
    doc_override_file=RUN/'page_detection_overrides.json'
    doc_settings=json.loads(doc_override_file.read_text('utf-8')).get(item['id'],{}) if doc_override_file.exists() else {}
    answer_mode=answer_document(item) or doc_settings.get('answer_only',False);answer_series=False;records=[];pages=[];issues=[];occurrences=Counter();last_n=None
    with fitz.open(source) as pdf:
        selected_numbers=select_scan_numbers(pdf,item,ident)
        for pn,page in enumerate(pdf,1):
            lines,method=choose_lines(page,item,pn)
            if selected_numbers and method=='ocr' and pn in selected_numbers:
                lines=[dict(l,number_disabled=True) if marker(l) and marker(l)[2]!='chinese' else l for l in lines]
                lines.extend(selected_numbers.get(pn,[]))
            override_file=RUN/'page_detection_overrides.json'
            overrides=json.loads(override_file.read_text('utf-8')).get(item['id'],{}) if override_file.exists() else {}
            if pn in overrides.get('authoritative_pages',[]):
                lines=[dict(l,number_disabled=True) if marker(l) and marker(l)[2]!='chinese' else l for l in lines]
            if pn in overrides.get('new_series',[]):
                current=None;last_n=None;chinese_parent=False;answer_series=False
            for entry in overrides.get('markers',[]):
                if entry['page']!=pn:continue
                lines=[dict(l,number_disabled=True) if marker(l) and marker(l)[0]==str(entry['number']) and abs(l['bbox'][1]-entry['bbox'][1])<.025 else l for l in lines]
                extra={'boundary_top':entry['top']} if 'top' in entry else {}
                extra['marker_style']='paren' if str(entry['number']).isdigit() else 'chinese'
                lines.append(dict(text='('+str(entry['number'])+')',bbox=entry['bbox'],method='visual_number',selected_question_number=str(entry['number']),selected_rest='',**extra))
            for entry in overrides.get('role_markers',[]):
                if entry['page']==pn:lines.append(dict(text='【解析】',bbox=entry['bbox'],method='visual_role',boundary_top=entry['bbox'][1]))
            seal=any(l['bbox'][3]-l['bbox'][1]>.25 and l['bbox'][2]<.14 for l in lines)
            usable=[l for l in lines if .025<l['bbox'][1]<.985 and l['bbox'][2]-l['bbox'][0]>.006 and not is_footer(l)
                    and not (l['bbox'][3]-l['bbox'][1]>.2 and l['bbox'][2]-l['bbox'][0]<.10)]
            pageid=f"{item['id']}-p{pn}"
            info=dict(id=pageid,document_id=item['id'],number=pn,width=page.rect.width,height=page.rect.height,
                      rotation=page.rotation,has_text=method=='text',method=method,anchors=0,unassigned=[])
            pages.append(info)
            if method=='ocr_missing':issues.append(dict(type='ocr_missing',page=pn))
            # A table of contents is not a list of exam questions.
            if sum('届' in l['text'] and '参考' in l['text'] for l in usable)>5:
                info['status']='contents';current=None;continue
            images=[]
            if method!='ocr':
                for b in page.get_text('dict')['blocks']:
                    if b['type']==1:
                        images.append([b['bbox'][0]/page.rect.width,b['bbox'][1]/page.rect.height,
                                       b['bbox'][2]/page.rect.width,b['bbox'][3]/page.rect.height])
            for lane_i,(left,right) in enumerate(lanes_for(usable)):
                if seal and left<.1:left=.13
                ordered=sorted([l for l in usable if left<=l['bbox'][0]<right],key=lambda l:(l['bbox'][1],l['bbox'][0]))
                if not ordered:continue
                # Avoid scoring instructions above the first actual competition problem.
                first_cn=next((l['bbox'][1] for l in ordered if marker(l) and marker(l)[2]=='chinese' and
                               not re.search('注意|说明',marker(l)[1])),None)
                candidates=[l for l in ordered if marker(l) and marker(l)[2]!='chinese']
                margins=Counter(round(l['bbox'][0]/.025)*.025 for l in candidates)
                main_margin=margins.most_common(1)[0][0] if margins else left+.15
                cursor=max(.025,min(l['bbox'][1] for l in ordered)-.012)
                bottom=min(.988,max(l['bbox'][3] for l in ordered)+.014)
                if method=='ocr':bottom=max(bottom,.958)
                lane_started=False

                def flush(end):
                    nonlocal cursor
                    if end<=cursor+.003:return
                    selected=[l for l in ordered if l['bbox'][3]>cursor+.003 and l['bbox'][1]<end-.002]
                    if not selected:return
                    if current:
                        region=dict(id=f"{current['id']}-r{len(current['regions'])+1}",page_id=pageid,
                                    bbox=[left,max(.01,cursor),right,min(.995,end+.002)],role=current['_role'])
                        current['regions'].append(region)
                        current['_text'][current['_role']].extend(l['text'] for l in selected)
                    else:info['unassigned'].append([left,cursor,right,end])

                for line in ordered:
                    text=line['text'];t=compact(text);m=marker(line);event=None;payload=None
                    if not chinese_parent and re.match(r'^解答[.．:：]?[(（](?:1|D)[)）]',t):
                        m=('1','解答:'+t,'paren');answer_series=True;last_n=None
                    newident=update_identity(ident,text)
                    if newident!=ident and not m:
                        event='identity';payload=newident
                    elif ANSWER_HEADING.fullmatch(t) and line['bbox'][0]>.2 and ident['category']!='北京交通大学课程考试':
                        event='answer_heading'
                    elif m:
                        n,rest,style=m
                        if style=='chinese':
                            sk=section_kind(rest)
                            if sk and '竞赛' in ident['category'] and re.search(r'[(（][^）)]*分[）)](?:设|求|证|已知|计算|若|给定)',compact(rest)):
                                sk=None
                            if ident['category']=='考研' and ident.get('year') and int(ident['year'])>=2004 and n in {'一','二','三'}:
                                sk={'一':'choice','二':'blank','三':'solution'}[n]
                            if sk:
                                if sk=='unknown' and ident['category']=='全国大学生数学竞赛' and ident['subject']=='非数学':sk='blank'
                                event='section';payload=(n,sk)
                            elif not re.search(r'注意事项|考试说明|要求',rest[:12]):event='question';payload=m
                        else:
                            # Parenthetical steps stay in their parent proof/problem.
                            if chinese_parent:continue
                            if style=='arabic' and current and last_n and int(n)<last_n and ident['category']=='考研' and ident.get('year') and int(ident['year'])>=2004 and not SOLUTION.match(compact(rest)):
                                continue
                            if style=='paren' and current and not SOLUTION.match(compact(rest)):
                                if last_n is not None and (int(n)<=last_n or
                                   (current['kind']=='solution' and int(n)<10 and last_n>=10)):continue
                            if first_cn is not None and line['bbox'][1]<first_cn and '竞赛' in ident['category']:continue
                            scoring_indent=(first_cn is not None and line['bbox'][1]<first_cn+.18 and int(n)<=2 and line['bbox'][0]<.5)
                            if line['bbox'][0]>main_margin+.055 and not (int(n)==1 and not current) and not scoring_indent and not line.get('selected_question_number'):continue
                            if style=='arabic' and not rest.strip():continue
                            if current and current['_role']!='stem' and style=='paren' and kind=='solution' and last_n and int(n)<last_n:continue
                            event='question';payload=m
                    elif SOLUTION.match(t) and current and line['bbox'][0]<left+.55 and re.search(r'答案|解析|解答|详解',item['name']):
                        event='solution';payload='answer' if '答案' in t[:8] or t.startswith('【答】') else 'explanation'
                        if current['_role']==payload:continue
                    if not event:continue
                    geometry=ordered+[l for l in native_lines(page) if left<=l['bbox'][0]<right] if method=='ocr' else ordered
                    top=safe_top(line,geometry,images,cursor if lane_started else 0)
                    top=min(top,bottom-.004)
                    flush(top)
                    if event=='identity':
                        ident=payload;current=None;segment+=1;kind='unknown';section='';chinese_parent=False;last_n=None
                        if not answer_document(item) and not doc_settings.get('answer_only'):answer_mode=False
                    elif event=='answer_heading':
                        current=None;answer_mode=True;chinese_parent=False;last_n=None
                    elif event=='section':
                        section,kind=payload;current=None;chinese_parent=False;last_n=None;answer_series=False
                    elif event=='question':
                        n,rest,style=payload
                        qkind='solution' if style=='chinese' else kind
                        if style=='chinese':answer_series=False
                        key=json.dumps([ident,section,n],ensure_ascii=False,sort_keys=True)
                        occurrences[key]+=1
                        qid=item['id']+'-q'+hashlib.sha256((key+'-'+str(occurrences[key])).encode()).hexdigest()[:12]
                        role='answer' if answer_mode or answer_series or SOLUTION.match(compact(rest)) else 'stem'
                        current=dict(id=qid,document_id=item['id'],number=n,kind=qkind,answer='',tags=[ident['category']]+([ident['year']+'年'] if ident.get('year') else []),
                                     reviewed=False,note='自动初切；题图边界与答案匹配分别待复核。',regions=[],identity=dict(ident),section=section,
                                     segment=segment,boundary_status='pending',answer_status='unmatched',grading_status='unverified',
                                     start=dict(page=pn,bbox=line['bbox'],text=text,style=style),
                                     _role=role,_text={'stem':[],'answer':[],'explanation':[]})
                        if style=='chinese':chinese_parent=True
                        records.append(current);info['anchors']+=1;last_n=int(n) if n.isdigit() else None
                    elif event=='solution':current['_role']=payload
                    cursor=top;lane_started=True
                flush(bottom)
            if info.get('status')!='contents':
                assigned=[r for q in records for r in q['regions'] if r['page_id']==pageid]
                info['status']='located' if info['anchors'] else 'continuation' if assigned else 'unresolved'
                if info['status']=='unresolved':issues.append(dict(type='unresolved_page',page=pn))
    result=[]
    for q in records:
        if not q['regions']:continue
        q['text_hint']='\n'.join(q['_text']['stem'])[:16000]
        q['answer_hint']='\n'.join(q['_text']['answer']+q['_text']['explanation'])[:16000]
        q.pop('_text');q.pop('_role')
        roles={r['role'] for r in q['regions']}
        q['record_type']='question' if 'stem' in roles else 'answer'
        if 'stem' in roles and len(roles)>1:q['answer_status']='inline_candidate'
        if q['kind']=='unknown' and q['identity']['category']=='考研' and q['identity'].get('year') and q['number'].isdigit():
            y=int(q['identity']['year']);n=int(q['number'])
            if y>=2003:q['kind']='choice' if n<=(10 if y>=2021 else 8) else 'blank' if n<=(16 if y>=2021 else 14) else 'solution'
        q['suspected_answer_in_stem']=bool(re.search(r'【\s*[A-Da-d]\s*】|【答案】|故选|正确答案',q['text_hint']))
        if q['identity']['category']=='北京交通大学课程考试' and '答案' in item['name']:
            q['suspected_answer_in_stem']=True
        if q['suspected_answer_in_stem']:q['note']+=' 原卷含答案，题干可能显示正确选项，尚未遮盖。'
        if len(q['regions'])>25:q['note']+=' 片段较多，优先检查是否漏识别后续题号。'
        result.append(q)
    value=dict(item=item,engine_version=VERSION,status='detected',pdf_path=source.relative_to(ROOT).as_posix(),
               pdf_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),pages=pages,questions=result,issues=issues)
    write(cache,value)
    print('DETECTED',item['name'],len(pages),'pages',len(result),'blocks',flush=True)
    return value


def match_key(q):
    i=q['identity']
    if not i.get('subject') or not (i.get('year') or i.get('edition')):return None
    # Section is needed where numbering restarts in older examinations.
    section=q['kind'] if q['kind']!='unknown' else q.get('section','')
    if i['category']=='考研' and i.get('year') and int(i['year'])<2003:
        section=q.get('section','') if q['number'].isdigit() else 'main'
    if i['category'] in {'北京交通大学数学竞赛','北京市大学生数学竞赛'}:
        section=q.get('section','') if q['number'].isdigit() else 'main'
    return (i['category'],i['subject'],i.get('year'),i.get('edition'),i.get('academic_term'),i.get('stage'),i.get('group'),i.get('session'),section,q['number'])


def associate(results):
    allq=[q for result in results for q in result['questions']]
    for q in allq:
        i=q['identity']
        if i['category']=='考研' and i.get('year') and int(i['year'])>=2004 and q['number'].isdigit():
            n=int(q['number']);modern=int(i['year'])>=2021
            if int(i['year'])<=2008:q['kind']='blank' if n<=6 else 'choice' if n<=14 else 'solution'
            else:q['kind']='choice' if n<=(10 if modern else 8) else 'blank' if n<=(16 if modern else 14) else 'solution'
    pool=defaultdict(list)
    docs={r['item']['id']:r for r in results}
    for q in allq:
        if any(r['role']!='stem' for r in q['regions']) and match_key(q):pool[match_key(q)].append(q)
    linked=0
    for q in allq:
        if q['record_type']!='question' or q['answer_status']!='unmatched':continue
        candidates=[a for a in pool.get(match_key(q),[]) if a['id']!=q['id']]
        unique={}
        for a in candidates:
            fingerprint=(docs[a['document_id']]['pdf_sha256'],tuple((r['page_id'].split('-p')[-1],tuple(r['bbox'])) for r in a['regions'] if r['role']!='stem'))
            unique.setdefault(fingerprint,a)
        candidates=list(unique.values())
        same_doc=[a for a in candidates if a['document_id']==q['document_id']]
        if same_doc:candidates=same_doc
        q['answer_candidates']=[dict(record_id=a['id'],document_id=a['document_id'],evidence='exam identity + subject/group/session/section/question number') for a in candidates]
        if len(candidates)==1:
            a=candidates[0]
            for r in a['regions']:
                if r['role']!='stem':q['regions'].append(dict(r,id=q['id']+'-a'+str(len(q['regions'])+1),linked_from=a['id']))
            q['answer_hint']=a['answer_hint'];q['answer_status']='matched_identity_pending_review';linked+=1
        elif candidates:q['answer_status']='ambiguous'
    return allq,linked


def resolve_cross_references(records):
    """Some original books say only 'same as Mathematics I, question 3'."""
    index=defaultdict(list)
    for q in records:
        if q['record_type']=='question':
            i=q['identity'];index[(i.get('year'),i.get('subject'),q['number'])].append(q)
    linked=0
    for q in records:
        m=re.search(r'同数学([一二三四])\s*[(（]\s*(\d+)\s*[)）]\s*题',compact(q.get('text_hint','')))
        if not m:continue
        candidates=[p for p in index[(q['identity'].get('year'),'数学'+m.group(1),m.group(2))] if p['id']!=q['id'] and '同数学' not in p['text_hint']]
        local=[p for p in candidates if p['document_id']==q['document_id']]
        if local:candidates=local
        q['cross_reference']=dict(subject='数学'+m.group(1),number=m.group(2),candidates=[p['id'] for p in candidates])
        if len(candidates)==1:
            source=candidates[0];old=copy.deepcopy(q['regions']);q['source_reference_regions']=old
            q['regions']=[dict(r,id=q['id']+'-xref'+str(n+1),linked_from=source['id']) for n,r in enumerate(source['regions']) if r['role']=='stem']+[r for r in old if r['role']!='stem']
            q['cross_reference']['status']='linked_pending_review';q['reviewed']=False;linked+=1
            q['note']+=' 原页引用其他试卷题目，已关联题干原图，待核对。'
        else:q['cross_reference']['status']='unresolved';q['note']+=' 原页仅引用其他题，尚需核对引用关系。'
    return linked


def import_manual(results):
    sample=json.loads((ROOT/'data/cropper_trial/sample.json').read_text('utf-8'))
    state=json.loads((ROOT/'data/cropper_trial/annotations.json').read_text('utf-8'))
    docs={d['id']:d for d in sample['documents']};pages={p['id']:p for p in sample['pages']}
    bypath={r['item']['path']:r for r in results};count=0
    for old in state['questions']:
        result=bypath.get(docs[old['document_id']]['path'])
        if not result:continue
        starts=[q for q in result['questions'] if q['number']==old['number'] and q.get('record_type')=='question']
        firstpage=pages[old['regions'][0]['page_id']]['number']
        starts=[q for q in starts if q['start']['page']==firstpage]
        if len(starts)==1:q=starts[0]
        else:
            q=dict(copy.deepcopy(old),id=result['item']['id']+'-manual-'+old['id'],document_id=result['item']['id'],
                   identity=exam_identity(result['item']),section='',segment=0,answer_status='unmatched',grading_status='unverified',
                   record_type='question',text_hint='',answer_hint='',start=dict(page=firstpage,bbox=old['regions'][0]['bbox'],text='manual',style='manual'))
            result['questions'].append(q)
        original_auto=copy.deepcopy(q['regions']);manual=[]
        for k,r in enumerate(old['regions']):
            p=pages[r['page_id']]
            manual.append(dict(r,id=q['id']+'-m'+str(k+1),page_id=result['item']['id']+'-p'+str(p['number'])))
        q['automatic_regions_before_manual']=original_auto
        q['regions']=manual+[r for r in original_auto if r['role']!='stem']
        q['reviewed']=old['reviewed'];q['boundary_status']='manual_confirmed' if old['reviewed'] else 'manual_pending'
        q['note']=old['note'];q['manual_source']=dict(path='data/cropper_trial/annotations.json',revision=state['revision'],question_id=old['id'])
        count+=1
    return count


def export_record(q,pages,docs,pdfcache):
    folder=RUN/'images'/q['document_id']/q['id'];folder.mkdir(parents=True,exist_ok=True)
    files={};rfiles=[]
    previous=PREVIOUS_RECORDS.get(q['id'],{})
    oldregions={r['id']:r for r in previous.get('regions',[])}
    def same_clip(a,b):
        return a and a.get('page_id')==b['page_id'] and a.get('bbox')==b['bbox'] and a.get('image') and (RUN/a['image']).is_file()
    for role in ('stem','answer','explanation'):
        role_regions=[r for r in q['regions'] if r['role']==role]
        old_role=[r for r in previous.get('regions',[]) if r['role']==role]
        if len(role_regions)==len(old_role) and role_regions and all(r['id']==o['id'] and same_clip(o,r) for r,o in zip(role_regions,old_role)) and previous.get('files',{}).get(role) and (RUN/previous['files'][role]).is_file():
            for r,o in zip(role_regions,old_role):r['image']=o['image']
            files[role]=previous['files'][role];continue
        pieces=[]
        for r in q['regions']:
            if r['role']!=role:continue
            p=pages[r['page_id']];did=p['document_id']
            if did not in pdfcache:
                if len(pdfcache)>10:
                    _,old=pdfcache.popitem();old.close()
                pdfcache[did]=fitz.open(ROOT/docs[did]['path'])
            page=pdfcache[did][p['number']-1]
            box=fitz.Rect(*(v*(page.rect.width if k%2==0 else page.rect.height) for k,v in enumerate(r['bbox'])))
            pix=page.get_pixmap(matrix=fitz.Matrix(2.2,2.2),clip=box,alpha=False,colorspace=fitz.csRGB)
            img=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            dest=folder/(r['id']+'.png')
            if dest.exists() and not same_clip(oldregions.get(r['id']),r):
                digest=hashlib.sha256(json.dumps([r['page_id'],r['bbox']]).encode()).hexdigest()[:10]
                dest=folder/(r['id']+'-'+digest+'.png')
            if not dest.exists():img.save(dest)
            r['image']=dest.relative_to(RUN).as_posix();rfiles.append(r['image']);pieces.append(img)
        if pieces:
            dest=folder/(role+'.png')
            if dest.exists():
                digest=hashlib.sha256(json.dumps([r['image'] for r in role_regions]).encode()).hexdigest()[:10]
                dest=folder/(role+'-'+digest+'.png')
            if len(pieces)==1:
                # One image can serve both the region and its complete role; no duplicate disk copy.
                files[role]=next(r['image'] for r in q['regions'] if r['role']==role)
            else:
                if not dest.exists():
                    combined=Image.new('RGB',(max(p.width for p in pieces),sum(p.height for p in pieces)+18*(len(pieces)-1)),'white')
                    y=0
                    for p in pieces:combined.paste(p,(0,y));y+=p.height+18
                    combined.save(dest)
                files[role]=dest.relative_to(RUN).as_posix()
    q['files']=files


def apply_visual_page_review(results):
    file=RUN/'visual_page_review.json'
    if not file.exists():return
    review=json.loads(file.read_text('utf-8'))
    docs={r['item']['id']:r for r in results}
    for result in results:
        for p in result['pages']:
            entry=review['nonquestion_pages'].get(p['id'])
            if entry:
                p['status']=entry['status'];p['visual_review']=entry['evidence']
        remaining={p['number'] for p in result['pages'] if p.get('status')=='unresolved'}
        result['issues']=[i for i in result['issues'] if i.get('type')!='unresolved_page' or i['page'] in remaining]
    for entry in review['continuations']:
        result=docs[entry['document_id']]
        candidates=[q for q in result['questions'] if q['number']==entry['number'] and q['start']['page']<entry['page']]
        q=max(candidates,key=lambda q:q['start']['page'])
        pid=entry['document_id']+'-p'+str(entry['page'])
        q['regions'].append(dict(id=q['id']+'-visual-tail',page_id=pid,bbox=entry['bbox'],role=entry['role'],evidence=entry['evidence']))
        q['note']+=' 已补接原卷广告插页后的末尾解答。'
        next(p for p in result['pages'] if p['id']==pid)['status']='continuation'
        result['issues']=[i for i in result['issues'] if not (i.get('type')=='unresolved_page' and i['page']==entry['page'])]


def finish(results):
    global PREVIOUS_RECORDS
    existing=RUN/'manifest.json'
    if existing.exists():
        old=json.loads(existing.read_text('utf-8'))
        PREVIOUS_RECORDS={q['id']:q for q in old['questions']+old['answer_records']}
        backup=RUN/'manifest_history'/('manifest-'+str(time.time_ns())+'.json');backup.parent.mkdir(exist_ok=True);backup.write_bytes(existing.read_bytes())
    apply_visual_page_review(results)
    manual=import_manual(results)
    records,linked=associate(results)
    cross_refs=resolve_cross_references(records)
    documents=[];pages=[]
    for r in results:
        if r['status']!='detected':continue
        i=r['item'];documents.append(dict(id=i['id'],name=i['name'],title=i['name'],hint='全量初切；可拖动边框、补画跨页片段。',
             path=r['pdf_path'],sha256=r['pdf_sha256'],original_path=i['path'],original_sha256=i['sha256'],format=i['format']))
        pages.extend(r['pages'])
    pmap={p['id']:p for p in pages};dmap={d['id']:d for d in documents};pdfcache={}
    errors=[]
    for n,q in enumerate(records,1):
        try:export_record(q,pmap,dmap,pdfcache)
        except Exception as exc:errors.append(dict(question_id=q['id'],error=str(exc)));q['export_error']=str(exc)
        if n%100==0:
            print('IMAGES',n,'/',len(records),flush=True)
            write(RUN/'progress.json',dict(stage='export',processed=n,total=len(records)))
    for pdf in pdfcache.values():pdf.close()
    catalog=[]
    for r in results:
        i=r['item'];qs=r['questions'];needed={reg['page_id'] for q in qs for reg in q['regions']}
        related={pmap[pid]['document_id'] for pid in needed}
        related.update(a['document_id'] for q in qs for a in q.get('answer_candidates',[]))
        workspacepages=sorted([p for p in pages if p['document_id']==i['id'] or p['document_id'] in related],key=lambda p:(p['document_id']!=i['id'],p['document_id'],p['number']))
        docids={p['document_id'] for p in workspacepages}
        sample=dict(schema_version=1,title=i['name'],bank_workspace=True,documents=sorted([d for d in documents if d['id'] in docids],key=lambda d:d['id']!=i['id']),
                    pages=workspacepages,questions=qs,seed_method='原页图像切分；自动坐标待核对；人工基线单独保留。')
        workspace=RUN/'workspaces'/i['id']
        write(workspace/'sample.json',sample)
        annotations=workspace/'annotations.json'
        if annotations.exists():
            prior=json.loads(annotations.read_text('utf-8'))
            if prior['revision']==0:
                backup=RUN/'workspace_build_history'/i['id']/('annotations-'+str(time.time_ns())+'.json');backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(annotations.read_bytes())
                write(annotations,dict(schema_version=1,revision=0,questions=qs,page_notes={p['id']:'' for p in workspacepages},updated_at=None))
        else:write(annotations,dict(schema_version=1,revision=0,questions=qs,page_notes={p['id']:'' for p in workspacepages},updated_at=None))
        catalog.append(dict(id=i['id'],name=i['name'],source=i['path'],status=r['status'],pages=len(r['pages']),
                            questions=sum(q.get('record_type')=='question' for q in qs),answers=sum(q.get('record_type')=='answer' for q in qs),
                            unmatched=sum(q.get('answer_status')=='unmatched' and q.get('record_type')=='question' for q in qs),
                            unresolved_pages=[p['number'] for p in r['pages'] if p.get('status')=='unresolved'],issues=r['issues']))
    manifest=dict(schema_version=2,engine_version=VERSION,status='automatic_draft',coordinate_system='normalized displayed PDF page; x0,y0,x1,y1',
                  documents=documents,pages=pages,questions=[q for q in records if q['record_type']=='question'],
                  answer_records=[q for q in records if q['record_type']=='answer'])
    write(RUN/'manifest.json',manifest);write(RUN/'catalog.json',catalog)
    summary=dict(documents_total=len(results),documents_processed=len(documents),pages=len(pages),
                 question_drafts=len(manifest['questions']),answer_records=len(manifest['answer_records']),manual_imported=manual,
                 manual_confirmed=sum(q['reviewed'] for q in records),answer_status=dict(Counter(q['answer_status'] for q in manifest['questions'])),
                 unresolved_pages=sum(p.get('status')=='unresolved' for p in pages),ocr_missing_pages=sum(p.get('method')=='ocr_missing' for p in pages),
                 export_errors=errors,answer_links=linked,cross_references_linked=cross_refs,source_manifest='inventory.json',completion='all sources processed; segmentation and matching drafts require review')
    write(RUN/'report.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


def main():
    global REFRESH
    parser=argparse.ArgumentParser();parser.add_argument('--ids',nargs='*');parser.add_argument('--detect-only',action='store_true');parser.add_argument('--finish',action='store_true');parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args();items=json.loads((OLD/'inventory.json').read_text('utf-8'))['files']
    REFRESH=args.refresh
    if args.ids:items=[i for i in items if i['id'] in args.ids]
    results=[]
    for i,item in enumerate(items,1):
        try:result=detect(item)
        except Exception as exc:
            result=dict(item=item,status='error',pages=[],questions=[],issues=[dict(type='detect_error',error=str(exc))]);print('ERROR',item['name'],str(exc),flush=True)
        results.append(result)
        write(RUN/'progress.json',dict(stage='detect',processed=i,total=len(items),last=item['name']))
    if not args.detect_only:finish(results)


if __name__=='__main__':main()
