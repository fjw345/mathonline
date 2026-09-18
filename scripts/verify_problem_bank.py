import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
db = sqlite3.connect(root / 'data' / 'problem_bank.sqlite3')
checks = {
    'sources': db.execute('select count(*) from problem_sources').fetchone()[0],
    'included_sources': db.execute('select count(*) from problem_sources where included=1').fetchone()[0],
    'excluded_monthly_sources': 0,
    'problems': db.execute('select count(*) from problems').fetchone()[0],
    'monthly_problems': 0,
    'problems_without_tags': db.execute('select count(*) from problems p where not exists (select 1 from problem_tags t where t.problem_id=p.id)').fetchone()[0],
    'problems_without_source': db.execute('select count(*) from problems p where not exists (select 1 from problem_sources s where s.id=p.source_id)').fetchone()[0],
    'issues': db.execute('select count(*) from extraction_issues').fetchone()[0],
    'tags': db.execute('select count(*) from tags').fetchone()[0],
}
# The parameter is deliberately constructed here so the source check cannot miss a Unicode path.
checks['excluded_monthly_sources'] = db.execute("select count(*) from problem_sources where included=0 and path like '%' || ? || '%'", ('月考',)).fetchone()[0]
checks['monthly_problems'] = db.execute("select count(*) from problems p join problem_sources s on s.id=p.source_id where s.path like '%' || ? || '%'", ('月考',)).fetchone()[0]
checks['monthly_source_links'] = db.execute("select count(*) from problem_source_links l join problem_sources s on s.id=l.source_id where s.path like '%' || ? || '%'", ('月考',)).fetchone()[0]
json_count = len(json.loads((root / 'data' / 'problems.json').read_text(encoding='utf-8')))
checks['json_problems'] = json_count
checks['problem_types'] = dict(db.execute('select question_type,count(*) from problems group by question_type order by question_type'))
checks['tag_dimensions'] = dict(db.execute('select dimension,count(*) from tags group by dimension order by dimension'))
checks['sources_without_problem_links'] = db.execute('select count(*) from problem_sources s where included=1 and not exists (select 1 from problem_source_links l where l.source_id=s.id)').fetchone()[0]
checks['source_links'] = db.execute('select count(*) from problem_source_links').fetchone()[0]
checks['merged_aliases'] = db.execute('select count(*) from problem_aliases').fetchone()[0]
checks['multi_source_problems'] = db.execute('select count(*) from (select problem_id from problem_source_links group by problem_id having count(*)>1)').fetchone()[0]
checks['dedup_review_candidates'] = db.execute('select count(*) from dedup_review_candidates').fetchone()[0]
checks['foreign_key_errors'] = len(db.execute('pragma foreign_key_check').fetchall())
checks['sqlite_quick_check'] = db.execute('pragma quick_check').fetchone()[0]
checks['ok'] = checks['monthly_problems'] == 0 and checks['monthly_source_links'] == 0 and checks['problems_without_tags'] == 0 and checks['problems_without_source'] == 0 and checks['sources_without_problem_links'] == 0 and checks['json_problems'] == checks['problems'] and checks['foreign_key_errors'] == 0 and checks['sqlite_quick_check'] == 'ok'
print(json.dumps(checks, ensure_ascii=False, indent=2))
if not checks['ok']:
    raise SystemExit(1)
