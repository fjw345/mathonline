"""Browse a complete extraction batch; reuse the established rectangle editor."""
from __future__ import annotations
import argparse
import copy
import hashlib
import html
import json
import math
import mimetypes
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/question_cropper'))
from app import Store, Handler, SAFE_ID, STATIC, json_bytes, Conflict

RUN=ROOT/'data/image_bank/runs/20261008-full-v2'
LABELS={'inline_candidate':'原卷内有答案/解析','matched_identity_pending_review':'按试卷身份匹配，待核对',
        'ambiguous':'多份答案候选，待选择','unmatched':'尚未匹配答案'}
STYLE='''body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f3f5f8;color:#19283a}main{max-width:1250px;margin:auto;padding:32px}h1{font-size:28px;margin:8px 0}p{color:#53627a}a{color:#155bbb}input{font:inherit;padding:12px;border:1px solid #bac6d5;border-radius:8px;width:min(90%,600px)}.metrics{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.metric,.card{padding:20px;background:white;border:1px solid #dfe4ed;border-radius:10px}.metric strong{font-size:24px;display:block}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #e4e8ef}td:first-child{max-width:650px}small{display:block;color:#647087}.actions{white-space:nowrap}.card{margin:20px 0}.card img{display:block;max-width:100%;max-height:950px;object-fit:contain;object-position:left top;margin:15px 0}.pill{padding:3px 8px;background:#eaf0fa;border-radius:5px;font-size:13px}.warning{color:#935900}nav{display:flex;gap:18px}details{margin-top:15px}summary{cursor:pointer}button{font:inherit;padding:6px 15px;cursor:pointer}'''


def page(title,body,script=''):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>'+html.escape(title)+'</title><style>'+STYLE+'</style><main>'+body+'</main><script>'+script+'</script></html>').encode('utf-8')


class BankStore(Store):
    """A bounded per-source workspace, with explicit cross-document answer regions."""
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir).resolve()
        self.sample=json.loads((self.data_dir/'sample.json').read_text('utf-8'))
        self.pages={p['id']:p for p in self.sample['pages']}
        self.documents={d['id']:d for d in self.sample['documents']}
        self.lock=threading.RLock();self.path=self.data_dir/'annotations.json';self.source_paths={}
        for doc in self.documents.values():
            source=(ROOT/doc['path']).resolve()
            if not (source.is_relative_to(ROOT/'resource') or source.is_relative_to(RUN/'converted')):
                raise ValueError('原页路径超出题库来源范围')
            original=(ROOT/doc['original_path']).resolve()
            original.relative_to(ROOT/'resource')
            if hashlib.sha256(original.read_bytes()).hexdigest()!=doc['original_sha256']:
                raise ValueError('原始文件已改变：'+doc['name'])
            if hashlib.sha256(source.read_bytes()).hexdigest()!=doc['sha256']:
                raise ValueError('渲染文件已改变：'+doc['name'])
            self.source_paths[doc['id']]=source
            doc.setdefault('tags',[])
        self.state=json.loads(self.path.read_text('utf-8'));self.validate(self.state)

    def validate(self,payload):
        if not isinstance(payload,dict) or payload.get('schema_version')!=1 or type(payload.get('revision')) is not int or payload['revision']<0:
            raise ValueError('标注格式或版本不正确')
        qs=payload.get('questions')
        if not isinstance(qs,list) or len(qs)>5000:raise ValueError('单份工作区题目数量不正确')
        qids=set();rids=set()
        for q in qs:
            if not isinstance(q,dict) or not isinstance(q.get('id'),str) or not SAFE_ID.fullmatch(q['id']) or q['id'] in qids:raise ValueError('题目 ID 无效或重复')
            qids.add(q['id'])
            if q.get('document_id') not in self.documents:raise ValueError('题目来源无效')
            for key,limit in [('number',40),('answer',2000),('note',4000)]:
                if not isinstance(q.get(key),str) or len(q[key])>limit:raise ValueError(key+' 内容无效')
            if not q['number'].strip() or q.get('kind') not in {'choice','blank','solution','unknown'} or type(q.get('reviewed')) is not bool:raise ValueError('题号、类型或复核状态无效')
            if not isinstance(q.get('tags'),list) or len(q['tags'])>30 or any(not isinstance(t,str) or len(t)>100 for t in q['tags']):raise ValueError('标签无效')
            regions=q.get('regions')
            if not isinstance(regions,list) or not 1<=len(regions)<=512:raise ValueError('每条记录需要 1–512 个片段')
            for r in regions:
                if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not SAFE_ID.fullmatch(r['id']) or r['id'] in rids:raise ValueError('片段 ID 无效或重复')
                rids.add(r['id'])
                if r.get('page_id') not in self.pages or r.get('role') not in {'stem','answer','explanation'}:raise ValueError('片段来源或类型无效')
                b=r.get('bbox')
                if not isinstance(b,list) or len(b)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in b):raise ValueError('坐标无效')
                if b[2]-b[0]<.002 or b[3]-b[1]<.002:raise ValueError('区域过小或方向错误')
        notes=payload.get('page_notes')
        if not isinstance(notes,dict) or any(k not in self.pages or not isinstance(v,str) or len(v)>4000 for k,v in notes.items()):raise ValueError('页面备注无效')

    def save(self,payload):
        self.validate(payload)
        with self.lock:
            if payload['revision']!=self.state['revision']:raise Conflict('另一窗口已保存，请先下载本地草稿后刷新核对。')
            candidate=dict(schema_version=1,revision=self.state['revision']+1,questions=copy.deepcopy(payload['questions']),
                           page_notes=copy.deepcopy(payload['page_notes']),updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
            # Every previous user revision remains a separate file.
            folder=self.data_dir/'history';folder.mkdir(exist_ok=True)
            backup=folder/f"annotations-r{self.state['revision']}.json"
            if not backup.exists():backup.write_bytes(json_bytes(self.state))
            self.path.write_bytes(json_bytes(candidate));self.state=candidate
            return dict(revision=candidate['revision'],updated_at=candidate['updated_at'])


class BankHandler(Handler):
    stores={}
    stores_lock=threading.Lock()

    def route_editor(self):
        parts=urlsplit(self.path).path.split('/')
        if len(parts)<4 or parts[1]!='edit':return False
        did=parts[2]
        if not SAFE_ID.fullmatch(did) or not (RUN/'workspaces'/did/'sample.json').is_file():return False
        with self.stores_lock:
            if did not in self.stores:self.stores[did]=BankStore(RUN/'workspaces'/did)
            self.store=self.stores[did]
        self.editor_prefix='/edit/'+did
        self.path='/'+'/'.join(parts[3:])
        return True

    def do_GET(self):
        if not self.local_request():return self.error('仅接受本机访问',403)
        original=self.path
        try:
            if self.route_editor():
                if self.path=='/':
                    content=(STATIC/'index.html').read_text('utf-8')
                    content=content.replace('/static/',self.editor_prefix+'/static/')
                    for old,new in [('切题工作台 · 小规模测试','全量题图 · 边框编辑'),('小规模测试 · 3 份试卷 / 5 页','整卷题图 · 题干 / 答案 / 解析'),('测试样本','本卷来源'),('第一轮：只检查前 10 题','检查与调整题目'),('本页测试记录','本页备注'),('例如：用时 3 分钟；第 4 题公式边缘难拖','例如：第 4 题需要补入下一页选项'),('正在读取样本…','正在读取原卷…'),('首轮只需检查边界','请核对配套答案'),('记录用时和不顺手的地方，方便下一轮改进。','记录本页的边界或答案问题。'),('首轮验证修正流程，确认边界不等于核实答案。','题框确认、答案核对和判分配置分别进行。')]:content=content.replace(old,new)
                    content=content.replace('<body>','<body><nav style="padding:8px 20px;background:#eef2f8"><a href="/">← 全部文件</a><span style="margin-left:20px">边界确认与答案核对分别进行；修改会保留历史版本。</span></nav>')
                    return self.reply(content.encode('utf-8'),'text/html; charset=utf-8')
                if self.path=='/static/app.js':
                    js=(STATIC/'app.js').read_text('utf-8').replace('/api/',self.editor_prefix+'/api/')
                    js=js.replace('样本 ${','原页 ${').replace('坐标和测试记录','坐标和页面备注')
                    js=js.replace('await showPage(sample.pages[0].id);','await showPage(state.questions[0]?.regions[0]?.page_id || sample.pages[0].id);')
                    js=js.replace('${location.origin}','${location.origin}${location.pathname}')
                    js=js.replace("if (mode === 'append' && currentQuestion()?.document_id !== currentPage().document_id)","if (false)")
                    js=js.replace("$('document').onchange=()=>{setMode('select');showPage(sample.pages.find(p=>p.document_id===$('document').value).id);};",
                                  "$('document').onchange=()=>{showPage(sample.pages.find(p=>p.document_id===$('document').value).id,mode==='append');};")
                    js=js.replace('可先切换同一试卷的页面，再画框。','可切换题卷或配套答案页面，再画框。')
                    js=js.replace('同一份试卷的页面','题卷或配套答案页面')
                    return self.reply(js.encode('utf-8'),'text/javascript; charset=utf-8')
                return super().do_GET()
            path=urlsplit(original).path
            if path=='/':return self.catalog()
            if path.startswith('/bank/'):return self.gallery(path.split('/')[2],parse_qs(urlsplit(original).query))
            if path.startswith('/files/'):
                file=(RUN/path[len('/files/'):]).resolve();file.relative_to(RUN)
                if file.suffix.lower() not in {'.png','.json','.csv','.md','.html'} or not file.is_file():return self.error('文件不存在',404)
                return self.reply(file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            if path=='/favicon.ico':return self.reply(b'','image/x-icon',204)
            return self.error('页面不存在',404)
        except Exception as exc:return self.error(str(exc),500)

    def do_POST(self):
        try:
            if not self.local_request() or self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.error('仅接受本机 JSON 请求',403)
            if not self.route_editor():return self.error('接口不存在',404)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=64_000_000:return self.error('请求大小不正确',413)
            payload=json.loads(self.rfile.read(length))
            if self.path=='/api/save':return self.reply(json_bytes(self.store.save(payload)))
            if self.path=='/api/export':
                if payload.get('revision')!=self.store.get()['state']['revision']:raise Conflict('请先保存最新修改')
                return self.reply(self.store.export(),'application/zip',attachment='question-crops.zip')
            self.error('接口不存在',404)
        except Conflict as exc:self.error(str(exc),409)
        except Exception as exc:self.error(str(exc),400)

    def catalog(self):
        catalog=json.loads((RUN/'catalog.json').read_text('utf-8'));report=json.loads((RUN/'report.json').read_text('utf-8'))
        body='<h1>全量题图工作台</h1><p>按原文件浏览题图与答案。自动切分仍需检查，已保留原页、坐标和人工试切结果。</p>'
        body+='<div class="metrics">'+''.join(f'<div class="metric"><strong>{report[k]}</strong>{label}</div>' for k,label in
             [('documents_processed','份来源文件'),('pages','页原卷'),('question_drafts','条题目记录'),('answer_records','条独立答案记录')])+'</div>'
        body+='<p><a href="/files/REPORT.md">查看提取报告与待核对项</a> · <a href="/files/manifest.json">完整坐标与图片清单</a></p>'
        body+='<input id="search" placeholder="搜索年份、数一、微积分或文件名" aria-label="搜索文件"><table><thead><tr><th>原文件</th><th>页数</th><th>题目 / 答案</th><th>操作</th></tr></thead><tbody>'
        for x in catalog:
            body+=f'<tr><td>{html.escape(x["name"])}<small>{html.escape(x["source"])}</small>'
            if x['unresolved_pages']:body+=f'<small class="warning">有 {len(x["unresolved_pages"])} 页尚未定位题号，保留原页待检查</small>'
            body+=f'</td><td>{x["pages"]}</td><td>{x["questions"]} / {x["answers"]}</td><td class="actions"><a href="/bank/{x["id"]}/">看题图</a> · <a href="/edit/{x["id"]}/">调整边框</a></td></tr>'
        body+='</tbody></table>'
        script="document.getElementById('search').oninput=e=>{const t=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(t))}"
        self.reply(page('全量题图工作台',body,script),'text/html; charset=utf-8')

    def gallery(self,did,query):
        if not SAFE_ID.fullmatch(did):return self.error('来源无效',404)
        file=RUN/'workspaces'/did/'sample.json'
        if not file.exists():return self.error('来源不存在',404)
        sample=json.loads(file.read_text('utf-8'));qs=sample['questions'];offset=max(0,int(query.get('offset',['0'])[0]));size=25
        body=f'<nav><a href="/">← 全部文件</a><a href="/edit/{did}/">调整边框与片段</a></nav><h1>{html.escape(sample["title"])}</h1><p>这里展示本批次初始题图。编辑后使用工作台“导出已确认题目”获取最新图片。</p>'
        body+=f'<p>第 {offset+1}–{min(offset+size,len(qs))} 条 / 共 {len(qs)} 条</p>'
        for q in qs[offset:offset+size]:
            title=('答案记录' if q.get('record_type')=='answer' else '题目')+' '+q['number']
            ident=q.get('identity',{});label=' · '.join(str(ident.get(k) or '') for k in ['year','subject','group'])
            body+=f'<article class="card"><h2>{html.escape(title)} <small>{html.escape(label)}</small></h2><span class="pill">{LABELS.get(q.get("answer_status"),"待核对")}</span>'
            if q.get('suspected_answer_in_stem'):body+='<p class="warning">原卷题干可能带有正确答案，尚未遮盖。</p>'
            files=q.get('files',{})
            if 'stem' in files:body+=f'<img loading="lazy" src="/files/{files["stem"]}" alt="题干原图">'
            if any(k in files for k in ['answer','explanation']):
                body+='<details><summary>查看答案 / 解析原图</summary>'
                for role in ['answer','explanation']:
                    if role in files:body+=f'<img loading="lazy" src="/files/{files[role]}" alt="答案或解析原图">'
                body+='</details>'
            refs=[]
            for r in q['regions']:
                p=next(p for p in sample['pages'] if p['id']==r['page_id'])
                d=next(d for d in sample['documents'] if d['id']==p['document_id'])
                refs.append(d['name']+' 第'+str(p['number'])+'页')
            body+='<small>来源：'+html.escape('；'.join(dict.fromkeys(refs)))+'</small></article>'
        if offset>0:body+=f'<a href="?offset={max(0,offset-size)}">上一页</a>　'
        if offset+size<len(qs):body+=f'<a href="?offset={offset+size}">下一页</a>'
        self.reply(page(sample['title'],body),'text/html; charset=utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8767);parser.add_argument('--open-browser',action='store_true');args=parser.parse_args()
    url=f'http://127.0.0.1:{args.port}/'
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),BankHandler)
    except OSError:
        if args.open_browser:
            import urllib.request,webbrowser
            with urllib.request.urlopen(url,timeout=3) as response:
                if '全量题图工作台' in response.read(8192).decode('utf-8'):
                    webbrowser.open(url);print('已打开正在运行的全量切题工作台。');return
        raise
    print(f'Image bank: http://127.0.0.1:{args.port}/',flush=True)
    if args.open_browser:
        import webbrowser
        threading.Timer(.4,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/')).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
