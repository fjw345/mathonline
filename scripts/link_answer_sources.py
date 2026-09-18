"""Attach answer-only source files to matching questions without creating duplicates."""
from __future__ import annotations
import json,re,sqlite3
from pathlib import Path
from build_problem_bank import DB_PATH,JSON_PATH,ROOT,export_json,read_doc

ANSWER=re.compile(r'^\s*(\d{1,2})\s*【\s*答案\s*】\s*(.+)$',re.M)
def clean_answer(value):
    value=value.strip();choice=re.match(r'[（(]\s*([A-Da-d])\s*[）)]',value)
    return choice.group(1).upper() if choice else value[:500]
def main():
    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');linked=updated=0;unmatched=[]
    sources=c.execute("select id,path,filename,source_kind from problem_sources where included=1 and filename like '%参考答案%'").fetchall()
    for source in sources:
        path=ROOT/source['path'];pages,_=read_doc(path);parent=str(Path(source['path']).parent).replace('\\','/')+'/%'
        for page_no,text in enumerate(pages,1):
            for match in ANSWER.finditer(text):
                number=int(match.group(1));answer=clean_answer(match.group(2))
                candidates=c.execute("select distinct p.id from problems p join problem_source_links l on l.problem_id=p.id join problem_sources s on s.id=l.source_id where s.path like ? and s.id<>? and p.question_number=?",(parent,source['id'],number)).fetchall()
                if len(candidates)!=1:unmatched.append({'source':source['filename'],'question_number':number,'candidate_count':len(candidates)});continue
                pid=candidates[0][0];c.execute('insert or ignore into problem_source_links(problem_id,source_id,source_page_start,source_page_end,source_question_number,extraction_method,is_primary) values(?,?,?,?,?,?,0)',(pid,source['id'],page_no,page_no,number,'answer-source-ocr'));linked+=c.execute('select changes()').fetchone()[0]
                c.execute('update problem_answers set standard_answer=?,grading_config_json=?,explanation_source=? where problem_id=? and standard_answer is null',(answer,json.dumps({'kind':'choice','correct_option_ids':[answer]} if re.fullmatch(r'[A-D]',answer) else {'kind':'self_assessment'},ensure_ascii=False),'answer_source',pid));updated+=c.execute('select changes()').fetchone()[0]
    c.commit();result={'answer_sources':len(sources),'source_links_added_this_run':linked,'answers_filled_this_run':updated,'total_answer_source_links':c.execute("select count(*) from problem_source_links where extraction_method='answer-source-ocr'").fetchone()[0],'total_answers_from_answer_sources':c.execute("select count(*) from problem_answers where explanation_source='answer_source'").fetchone()[0],'unmatched':unmatched};c.close();JSON_PATH.write_text(json.dumps(export_json(),ensure_ascii=False,indent=2),encoding='utf-8');(ROOT/'data'/'answer_link_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
