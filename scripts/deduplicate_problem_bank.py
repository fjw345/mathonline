"""Merge high-confidence duplicate problems while preserving all provenance."""
from __future__ import annotations
import hashlib, json, math, re, sqlite3, unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data'/'problem_bank.sqlite3';REPORT=ROOT/'data'/'dedup_report.json'
def canonical(text):
    text=unicodedata.normalize('NFKC',text).lower();text=re.sub(r'^\s*[（(]?\d{1,2}[）)、.．:：]?','',text);return ''.join(ch for ch in text if ch.isalnum() or '\u4e00'<=ch<='\u9fff')
def simhash(text):
    grams=[text[i:i+3] for i in range(max(1,len(text)-2))];vec=[0]*64
    for gram in grams:
        value=int.from_bytes(hashlib.blake2b(gram.encode('utf-8'),digest_size=8).digest(),'big')
        for bit in range(64):vec[bit]+=1 if value>>bit&1 else -1
    return sum((1<<bit) for bit,value in enumerate(vec) if value>=0)
def find_groups(rows):
    parent={r['id']:r['id'] for r in rows}
    def root(x):
        while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
        return x
    def union(a,b):
        a,b=root(a),root(b)
        if a!=b:parent[b]=a
    exact=defaultdict(list)
    for r in rows:
        if len(r['canon'])>=24:exact[r['canon']].append(r['id'])
    exact_pairs=0
    for ids in exact.values():
        if len(ids)>1:
            for other in ids[1:]:union(ids[0],other);exact_pairs+=1
    buckets=defaultdict(list)
    for r in rows:
        if len(r['canon'])<80:continue
        fp=simhash(r['canon']);r['simhash']=fp
        for part in range(8):buckets[(r['type'],part,(fp>>(part*8))&255)].append(r)
    checked=set();auto=[];review=[]
    for bucket in buckets.values():
        for i,a in enumerate(bucket):
            for b in bucket[i+1:]:
                key=tuple(sorted((a['id'],b['id'])))
                if key in checked or root(a['id'])==root(b['id']):continue
                checked.add(key);ratio_len=min(len(a['canon']),len(b['canon']))/max(len(a['canon']),len(b['canon']))
                if ratio_len<0.88 or (a['simhash']^b['simhash']).bit_count()>12:continue
                score=SequenceMatcher(None,a['canon'],b['canon'],autojunk=False).ratio()
                if score>=0.985:union(a['id'],b['id']);auto.append((*key,score))
                elif score>=0.94:review.append((*key,score))
    groups=defaultdict(list)
    for r in rows:groups[root(r['id'])].append(r['id'])
    return [x for x in groups.values() if len(x)>1],exact_pairs,auto,review
def main():
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on')
    existing=c.execute('select count(*) from problem_aliases').fetchone()[0]
    if existing:
        result={'already_deduplicated':True,'after':c.execute('select count(*) from problems').fetchone()[0],'merged':existing,'review_candidates':c.execute('select count(*) from dedup_review_candidates').fetchone()[0],'multi_source_problems':c.execute('select count(*) from (select problem_id from problem_source_links group by problem_id having count(*)>1)').fetchone()[0]};c.close();print(json.dumps(result,ensure_ascii=False,indent=2));return
    c.execute('delete from dedup_review_candidates')
    rows=[dict(x) for x in c.execute("select p.id,p.question_type type,p.content_markdown,case when a.standard_answer is not null then 1 else 0 end has_answer,case when a.explanation_markdown is not null then 1 else 0 end has_explanation,case when p.options_json is not null then 1 else 0 end has_options from problems p left join problem_answers a on a.problem_id=p.id")]
    for r in rows:r['canon']=canonical(r['content_markdown'])
    groups,exact_pairs,near,review=find_groups(rows);by_id={r['id']:r for r in rows};merged=0
    for group in groups:
        group.sort(key=lambda x:(by_id[x]['has_answer']*5+by_id[x]['has_explanation']*3+by_id[x]['has_options']*2+len(by_id[x]['canon'])),reverse=True);keep=group[0]
        for old in group[1:]:
            score=SequenceMatcher(None,by_id[keep]['canon'],by_id[old]['canon'],autojunk=False).ratio();match='exact' if by_id[keep]['canon']==by_id[old]['canon'] else 'near'
            c.execute('insert or ignore into problem_tags(problem_id,tag_id) select ?,tag_id from problem_tags where problem_id=?',(keep,old))
            c.execute('insert or ignore into problem_source_links(problem_id,source_id,source_page_start,source_page_end,source_question_number,extraction_method,is_primary) select ?,source_id,source_page_start,source_page_end,source_question_number,extraction_method,0 from problem_source_links where problem_id=?',(keep,old))
            ka=c.execute('select standard_answer,grading_config_json,explanation_markdown,explanation_source from problem_answers where problem_id=?',(keep,)).fetchone();oa=c.execute('select standard_answer,grading_config_json,explanation_markdown,explanation_source from problem_answers where problem_id=?',(old,)).fetchone()
            if oa and ka:c.execute('update problem_answers set standard_answer=coalesce(standard_answer,?),grading_config_json=case when standard_answer is null then ? else grading_config_json end,explanation_markdown=coalesce(explanation_markdown,?),explanation_source=case when explanation_markdown is null then ? else explanation_source end where problem_id=?',(oa[0],oa[1],oa[2],oa[3],keep))
            alias_answer=json.dumps(dict(zip(('standard_answer','grading_config_json','explanation_markdown','explanation_source'),oa)),ensure_ascii=False) if oa else None
            c.execute('delete from problems where id=?',(old,));c.execute('insert into problem_aliases(alias_problem_id,canonical_problem_id,match_type,similarity,alias_content_markdown,alias_answer_json) values(?,?,?,?,?,?)',(old,keep,match,score,by_id[old]['content_markdown'],alias_answer));merged+=1
    live={x[0] for x in c.execute('select id from problems')}
    for a,b,score in review:
        if a in live and b in live:c.execute('insert or ignore into dedup_review_candidates values(?,?,?)',(a,b,score))
    c.commit();result={'before':len(rows),'after':c.execute('select count(*) from problems').fetchone()[0],'merged':merged,'duplicate_groups':len(groups),'exact_duplicate_links':exact_pairs,'near_auto_pairs':len(near),'review_candidates':c.execute('select count(*) from dedup_review_candidates').fetchone()[0],'multi_source_problems':c.execute('select count(*) from (select problem_id from problem_source_links group by problem_id having count(*)>1)').fetchone()[0]}
    audit=[{'alias_id':x[0],'canonical_id':x[1],'similarity':x[2],'alias_text':x[3],'canonical_text':x[4]} for x in c.execute("select a.alias_problem_id,a.canonical_problem_id,a.similarity,a.alias_content_markdown,p.content_markdown from problem_aliases a join problems p on p.id=a.canonical_problem_id where a.match_type='near' order by a.similarity")]
    c.close();(ROOT/'data'/'near_merge_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    from build_problem_bank import JSON_PATH,export_json
    JSON_PATH.write_text(json.dumps(export_json(),ensure_ascii=False,indent=2),encoding='utf-8');REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
