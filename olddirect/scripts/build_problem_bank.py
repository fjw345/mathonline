"""Build a reviewable local question bank from the supplied exam resources."""
from __future__ import annotations
import hashlib, json, re, sqlite3, unicodedata
from pathlib import Path
from typing import Iterable
import fitz

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT, DATA_ROOT = ROOT / "resource", ROOT / "data"
WORD_TEXT_ROOT = DATA_ROOT / "word_text"
OCR_TEXT_ROOT = DATA_ROOT / "ocr_text"
DB_PATH, JSON_PATH, REPORT_PATH = DATA_ROOT / "problem_bank.sqlite3", DATA_ROOT / "problems.json", DATA_ROOT / "import_report.json"
MONTHLY = "\u6708\u8003"
RE_YEARS = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
RE_EDITION = re.compile(r"\u7b2c\s*(\d{1,2})\s*\u5c4a")
RE_Q = re.compile(r"^\s*(?:[\uff08(]\s*)?(\d{1,2})(?:\s*[\uff09)])\s*[\u3001.\uff0e:]?\s*(.*)$")
RE_Q2 = re.compile(r"^\s*(\d{1,2})\s*[\u3001.\uff0e]\s*(.*)$")
RE_Q3 = re.compile(r"^\s*(\d{1,2})\s+([\u4e00-\u9fff].{3,})$")
RE_SECTION = re.compile(r"(\u9009\u62e9\u9898|\u5355\u9879\u9009\u62e9\u9898|\u591a\u9879\u9009\u62e9\u9898|\u586b\u7a7a\u9898|\u89e3\u7b54\u9898|\u8ba1\u7b97\u9898|\u8bc1\u660e\u9898|\u7efc\u5408\u9898|\u5224\u65ad\u9898)")
RE_ANSWER = re.compile(r"(?:【\s*\u7b54\u6848\s*】|\[\s*\u7b54\u6848\s*\]|\u7b54\u6848\s*[:\uff1a])\s*(.*)", re.S)
RE_SOLUTION = re.compile(r"(?:【\s*\u89e3\u6790\s*】|\[\s*\u89e3\u6790\s*\]|\u89e3\u6790\s*[:\uff1a]|\u89e3\s*[:\uff1a])\s*(.*)", re.S)
RE_OPTION = re.compile(r"(?:^|\s)[\uff08(]\s*([A-Da-d])\s*[\uff09)]\s*", re.M)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS problem_sources (id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,source_kind TEXT NOT NULL,included INTEGER NOT NULL,exclusion_reason TEXT,sha256 TEXT NOT NULL,page_count INTEGER,text_page_count INTEGER,rights_status TEXT NOT NULL DEFAULT 'needs_verification',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS problems (id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES problem_sources(id),external_id TEXT NOT NULL UNIQUE,question_number INTEGER NOT NULL,question_type TEXT NOT NULL DEFAULT 'unknown',content_markdown TEXT NOT NULL,options_json TEXT,source_page_start INTEGER NOT NULL,source_page_end INTEGER NOT NULL,difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),status TEXT NOT NULL DEFAULT 'draft',extraction_method TEXT NOT NULL,requires_review INTEGER NOT NULL DEFAULT 1,answer_version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS problem_answers (problem_id TEXT PRIMARY KEY REFERENCES problems(id) ON DELETE CASCADE,standard_answer TEXT,grading_config_json TEXT,explanation_markdown TEXT,explanation_source TEXT NOT NULL DEFAULT 'source_or_unavailable',verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT,dimension TEXT NOT NULL,name TEXT NOT NULL,UNIQUE(dimension,name));
CREATE TABLE IF NOT EXISTS problem_tags (problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,PRIMARY KEY(problem_id,tag_id));
CREATE TABLE IF NOT EXISTS extraction_issues (id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT REFERENCES problem_sources(id),page INTEGER,issue_type TEXT NOT NULL,details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS problem_source_links (problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,source_id TEXT NOT NULL REFERENCES problem_sources(id),source_page_start INTEGER NOT NULL,source_page_end INTEGER NOT NULL,source_question_number INTEGER NOT NULL,extraction_method TEXT NOT NULL,is_primary INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(problem_id,source_id,source_page_start,source_question_number));
CREATE TABLE IF NOT EXISTS problem_aliases (alias_problem_id TEXT PRIMARY KEY,canonical_problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,match_type TEXT NOT NULL,similarity REAL NOT NULL,alias_content_markdown TEXT,alias_answer_json TEXT);
CREATE TABLE IF NOT EXISTS dedup_review_candidates (problem_id_a TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,problem_id_b TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,similarity REAL NOT NULL,PRIMARY KEY(problem_id_a,problem_id_b));
CREATE INDEX IF NOT EXISTS idx_problems_source ON problems(source_id);
CREATE INDEX IF NOT EXISTS idx_problems_filter ON problems(status,difficulty,question_type);
CREATE INDEX IF NOT EXISTS idx_problem_tags_tag ON problem_tags(tag_id);
"""
def u(code): return code
def sid(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
    return h.hexdigest()
def norm(text):
    text=unicodedata.normalize("NFKC",text).replace("\r","").replace("\u00a0"," ")
    return "\n".join(x for x in (re.sub(r"[ \t]+"," ",z).strip() for z in text.splitlines()) if x)
def source_kind(path):
    s=str(path)
    if "\u8003\u7814\u6570\u5b66" in s:
        m=re.search(r"\u6570\u5b66\s*[\uff08(]?([\u4e00\u4e8c\u4e09])[\uff09)]?",s); return u("\u8003\u7814\u6570\u5b66")+(m.group(1) if m else u("\u672a\u5206\u7c7b"))
    if "\u5168\u56fd\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b" in s: return u("\u5168\u56fd\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b")
    if "\u5317\u4ea4\u5927\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b" in s: return u("\u5317\u4ea4\u5927\u6570\u5b66\u7ade\u8d5b")
    if "\u5317\u4eac\u5e02\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b" in s: return u("\u5317\u4eac\u5e02\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b")
    if "\u5317\u4ea4\u5927\u5386\u5e74\u671f\u672b\u771f\u9898" in s: return u("\u5317\u4ea4\u671f\u672b\u8003")
    return u("\u5176\u4ed6\u6570\u5b66\u8bd5\u9898")
def years(path,text):
    ys=sorted({int(x) for x in RE_YEARS.findall(path.name) if 1900<=int(x)<=2100}) or sorted({int(x) for x in RE_YEARS.findall(text[:3000]) if 1900<=int(x)<=2100})
    if ys:return ys
    es=[int(x) for x in RE_EDITION.findall(path.name)]; return [2008+es[0]] if es else []
def subjects(path,content,kind):
    s=f"{path} {content[:500]}"; out=[]
    if re.search(r"\u77e9\u9635|\u884c\u5217\u5f0f|\u5411\u91cf|\u7279\u5f81\u503c|\u7279\u5f81\u5411\u91cf|\u7ebf\u6027\u65b9\u7a0b|\u4e8c\u6b21\u578b|\u7ebf\u6027\u7a7a\u95f4|\u79e9|\u6b63\u4ea4|\u76f8\u4f3c\u77e9\u9635|\u53ef\u9006",s):out.append(u("\u7ebf\u6027\u4ee3\u6570"))
    if re.search(r"\u6982\u7387|\u968f\u673a\u53d8\u91cf|\u5206\u5e03|\u671f\u671b|\u65b9\u5dee|\u534f\u65b9\u5dee|\u72ec\u7acb|\u5bc6\u5ea6|\u6837\u672c|\u4f30\u8ba1|\u7f6e\u4fe1|\u7edf\u8ba1\u91cf|\u6cca\u677e|\u6b63\u6001|\u4e8c\u9879",s):out.append(u("\u6982\u7387\u8bba\u4e0e\u6570\u7406\u7edf\u8ba1"))
    if "\u6982\u7387\u7edf\u8ba1" in s and u("\u6982\u7387\u8bba\u4e0e\u6570\u7406\u7edf\u8ba1") not in out:out.append(u("\u6982\u7387\u8bba\u4e0e\u6570\u7406\u7edf\u8ba1"))
    if "\u51e0\u4f55\u4e0e\u4ee3\u6570" in s and u("\u7ebf\u6027\u4ee3\u6570") not in out:out.append(u("\u7ebf\u6027\u4ee3\u6570"))
    if not out or kind.startswith(u("\u8003\u7814\u6570\u5b66")) or "\u5fae\u79ef\u5206" in s:out.append(u("\u9ad8\u7b49\u6570\u5b66"))
    return sorted(set(out))
def source_tags(path,content,kind):
    s=f"{path} {content[:300]}"; out=[]
    if kind==u("\u5317\u4ea4\u5927\u671f\u672b\u8003"):out.append(u("\u5317\u4ea4\u671f\u672b\u8003"))
    elif kind.startswith(u("\u8003\u7814\u6570\u5b66")):out.append({"\u4e00":u("\u8003\u7814\u6570\u4e00"),"\u4e8c":u("\u8003\u7814\u6570\u4e8c"),"\u4e09":u("\u8003\u7814\u6570\u4e09")}.get(kind[-1],kind))
    elif kind==u("\u5168\u56fd\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b"):
        x=u("\u5168\u56fd\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b")+(u("\u975e\u6570") if re.search(r"\u975e\u6570\u5b66|\u975e\u6570",s) else u("\u6570\u5b66")); out.append(x+('A' if 'A' in s else 'B' if 'B' in s else ''))
    elif kind in {u("\u5317\u4ea4\u5927\u6570\u5b66\u7ade\u8d5b"),u("\u5317\u4eac\u5e02\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b")}:
        out.append(kind)
        if kind.startswith(u("\u5317\u4eac\u5e02")):
            if "\u7532\u7ec4" in s:out.append(kind+u("\u7532\u7ec4"))
            if "\u4e59\u7ec4" in s:out.append(kind+u("\u4e59\u7ec4"))
    return out
def diff(kind,qtype):
    hard={u("\u89e3\u7b54\u9898"),u("\u8bc1\u660e\u9898"),u("\u7efc\u5408\u9898"),u("\u8ba1\u7b97\u9898")}; return 5 if kind==u("\u5168\u56fd\u5927\u5b66\u751f\u6570\u5b66\u7ade\u8d5b") and qtype in hard else 4 if qtype in hard or "\u7ade\u8d5b" in kind else 3
def read_doc(path):
    if path.suffix.lower()=='.pdf':
        d=fitz.open(path);pages=[p.get_text('text') for p in d];side=OCR_TEXT_ROOT/(str(path.relative_to(RESOURCE_ROOT))+'.json')
        if side.exists():
            payload=json.loads(side.read_text(encoding='utf-8'));ocr=payload.get('pages',[])
            if payload.get('replace_all'):pages=[ocr[i] if i<len(ocr) else text for i,text in enumerate(pages)]
            else:pages=[text if norm(text) else (ocr[i] if i<len(ocr) else text) for i,text in enumerate(pages)]
            return pages,'pymupdf+ocr'
        return pages,'pymupdf'
    if path.suffix.lower()=='.docx':
        from docx import Document; d=Document(path); return ['\n'.join(p.text for p in d.paragraphs)],'python-docx'
    side=WORD_TEXT_ROOT/(str(path.relative_to(RESOURCE_ROOT))+'.txt'); return ([side.read_text(encoding='utf-8',errors='replace')],'word-com') if side.exists() else ([], 'unavailable')
def split_questions(pages):
    current=None; qtype='unknown'
    for page_no,raw in enumerate(pages,1):
        for line in norm(raw).splitlines():
            msec=RE_SECTION.search(line)
            if msec:
                qtype=msec.group(1)
                if qtype in {u("\u5355\u9879\u9009\u62e9\u9898"),u("\u591a\u9879\u9009\u62e9\u9898")}:qtype=u("\u9009\u62e9\u9898")
            m=RE_Q.match(line) or RE_Q2.match(line) or RE_Q3.match(line)
            if m and m.group(2).strip():
                if current is not None:yield current
                current={'number':int(m.group(1)),'type':qtype,'start':page_no,'end':page_no,'lines':[line]}
            elif current is not None:current['lines'].append(line);current['end']=page_no
    if current is not None:yield current
def parse_block(block):
    text=norm('\n'.join(block['lines'])); explanation=answer=None
    ms=RE_SOLUTION.search(text)
    if ms:explanation=ms.group(1).strip();text=text[:ms.start()].strip()
    ma=RE_ANSWER.search(text)
    if ma:
        answer_text=ma.group(1).strip(); answer=answer_text.splitlines()[0] if answer_text else None; text=text[:ma.start()].strip()
    marks=list(RE_OPTION.finditer(text)); opts=[]
    if len(marks)>=2:
        for i,m in enumerate(marks):opts.append({'id':m.group(1).upper(),'text':text[m.end():(marks[i+1].start() if i+1<len(marks) else len(text))].strip()})
    return text,json.dumps(opts,ensure_ascii=False) if opts else None,answer,explanation
def export_json():
    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row;rows=c.execute('SELECT p.*,s.path source_path,s.source_kind,a.standard_answer,a.grading_config_json,a.explanation_markdown,a.explanation_source FROM problems p JOIN problem_sources s ON s.id=p.source_id LEFT JOIN problem_answers a ON a.problem_id=p.id ORDER BY s.path,p.source_page_start,p.question_number').fetchall();out=[]
    for r in rows:
        x=dict(r);ts=c.execute('SELECT t.dimension,t.name FROM tags t JOIN problem_tags pt ON pt.tag_id=t.id WHERE pt.problem_id=? ORDER BY t.dimension,t.name',(r['id'],)).fetchall();x['tags']=[{'dimension':t[0],'name':t[1]} for t in ts]
        if c.execute("select 1 from sqlite_master where type='table' and name='problem_source_links'").fetchone():
            links=c.execute('SELECT s.path,s.source_kind,l.source_page_start,l.source_page_end,l.source_question_number,l.extraction_method,l.is_primary FROM problem_source_links l JOIN problem_sources s ON s.id=l.source_id WHERE l.problem_id=? ORDER BY l.is_primary DESC,s.path,l.source_page_start',(r['id'],)).fetchall();x['sources']=[{'path':z[0],'kind':z[1],'page_start':z[2],'page_end':z[3],'question_number':z[4],'extraction_method':z[5],'is_primary':bool(z[6])} for z in links]
        out.append(x)
    c.close();return out
def build():
    DATA_ROOT.mkdir(exist_ok=True);c=sqlite3.connect(DB_PATH);c.executescript(SCHEMA)
    alias_columns={row[1] for row in c.execute('pragma table_info(problem_aliases)')}
    if 'alias_content_markdown' not in alias_columns:c.execute('alter table problem_aliases add column alias_content_markdown text')
    if 'alias_answer_json' not in alias_columns:c.execute('alter table problem_aliases add column alias_answer_json text')
    for table in ('problem_aliases','dedup_review_candidates','problem_source_links','problem_tags','problem_answers','problems','extraction_issues','problem_sources','tags'):c.execute(f'DELETE FROM {table}')
    files=sorted(p for p in RESOURCE_ROOT.rglob('*') if p.is_file() and p.suffix.lower() in {'.pdf','.doc','.docx'});excluded=[p for p in files if MONTHLY in str(p)];report={'resource_root':str(RESOURCE_ROOT),'included_files':len(files)-len(excluded),'excluded_monthly_files':len(excluded),'questions':0,'issues':0,'sources':[]}
    for path in files:
        inc=path not in excluded;source_id=sid(str(path.relative_to(ROOT)).replace('\\','/'));pages,method=read_doc(path) if inc else ([], 'excluded');text_pages=sum(bool(norm(x)) for x in pages);kind=source_kind(path);rel=str(path.relative_to(ROOT)).replace('\\','/');reason=None if inc else u("\u5317\u4ea4\u5927\u6708\u8003\u9898\u76ee\u6309\u7528\u6237\u8981\u6c42\u6392\u9664")
        c.execute('INSERT INTO problem_sources(id,path,filename,source_kind,included,exclusion_reason,sha256,page_count,text_page_count) VALUES(?,?,?,?,?,?,?,?,?)',(source_id,rel,path.name,kind,int(inc),reason,digest(path),len(pages) if inc else None,text_pages if inc else None));entry={'id':source_id,'path':rel,'kind':kind,'included':inc,'questions':0,'text_pages':text_pages};report['sources'].append(entry)
        if not inc:continue
        if not pages:c.execute('INSERT INTO extraction_issues(source_id,issue_type,details) VALUES(?,?,?)',(source_id,'missing_text','No Word sidecar text found'));report['issues']+=1;continue
        file_years=years(path,'\n'.join(pages))
        seen_external = {}
        for block in split_questions(pages):
            content,options,answer,explanation=parse_block(block)
            if len(content)<8:c.execute('INSERT INTO extraction_issues(source_id,page,issue_type,details) VALUES(?,?,?,?)',(source_id,block['start'],'short_block',content[:200]));report['issues']+=1;continue
            ext_base=f"{source_id}:p{block['start']}-{block['end']}:q{block['number']}:{sid(content[:160])}";seen_external[ext_base]=seen_external.get(ext_base,0)+1;ext=ext_base if seen_external[ext_base]==1 else f"{ext_base}:dup{seen_external[ext_base]}";pid=sid(ext);qtype=block['type'];d=diff(kind,qtype);ys=file_years or years(path,content)
            c.execute('INSERT INTO problems(id,source_id,external_id,question_number,question_type,content_markdown,options_json,source_page_start,source_page_end,difficulty,status,extraction_method,requires_review) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,source_id,ext,block['number'],qtype,content,options,block['start'],block['end'],d,'draft',method,1));grading={'kind':'choice','correct_option_ids':[answer.upper()]} if answer and re.fullmatch(r'[A-Da-d]',answer.strip()) else {'kind':'self_assessment'};c.execute('INSERT INTO problem_answers(problem_id,standard_answer,grading_config_json,explanation_markdown,explanation_source) VALUES(?,?,?,?,?)',(pid,answer,json.dumps(grading,ensure_ascii=False),explanation,'source' if answer or explanation else 'unavailable'))
            c.execute('INSERT INTO problem_source_links(problem_id,source_id,source_page_start,source_page_end,source_question_number,extraction_method,is_primary) VALUES(?,?,?,?,?,?,1)',(pid,source_id,block['start'],block['end'],block['number'],method))
            st=source_tags(path,content,kind);tags=st+subjects(path,content,kind)+[f'{y}\u5e74' for y in ys]+[f'\u96be\u5ea6{d}\u661f']
            for name in sorted(set(tags)):
                if '\\u' in name:name=name.encode('ascii').decode('unicode_escape')
                dim='year' if re.fullmatch(r'\d{4}\u5e74',name) else 'difficulty' if name.startswith(u("\u96be\u5ea6")) else 'source' if name in st else 'subject';c.execute('INSERT OR IGNORE INTO tags(dimension,name) VALUES(?,?)',(dim,name));tid=c.execute('SELECT id FROM tags WHERE dimension=? AND name=?',(dim,name)).fetchone()[0];c.execute('INSERT INTO problem_tags(problem_id,tag_id) VALUES(?,?)',(pid,tid))
            report['questions']+=1;entry['questions']+=1
    c.commit();report['tag_count']=c.execute('SELECT COUNT(*) FROM tags').fetchone()[0];report['draft_questions']=c.execute("SELECT COUNT(*) FROM problems WHERE status='draft'").fetchone()[0];c.close();JSON_PATH.write_text(json.dumps(export_json(),ensure_ascii=False,indent=2),encoding='utf-8');REPORT_PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');return report
if __name__=='__main__':
    result=build()
    print(json.dumps({key:value for key,value in result.items() if key!='sources'},ensure_ascii=False,indent=2))
