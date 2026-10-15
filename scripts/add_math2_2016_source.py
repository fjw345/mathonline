from pathlib import Path
import json,hashlib,sys
import fitz
import build_image_bank_v2 as e
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data/math2_2010_2026'
e.RUN=RUN
path=RUN/'sources/2016-xdf-combined.pdf'
item=dict(id='2016-xdf',name='2016年全国硕士研究生入学统一考试数学二试题及答案（新东方）',path=path.relative_to(ROOT).as_posix(),format='pdf',pages=len(fitz.open(path)),year=2016,mode='combined',original_path=path.relative_to(ROOT).as_posix(),original_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),page_offset=0,source_url='https://file.xdf.cn/uploads/151228/124_151228170532e5Y1wqxy5kiH4SBz.pdf',retrieved_date='2026-10-14',publisher='新东方在线',source_status='publisher_reference_not_official_answer',identity_evidence='第一页标题2016数学二；第1题无穷小阶排序，第19题二阶微分方程，第23题矩阵幂，与2014卷不同',alternative_source_note='同站5页独立题卷选项重复、缺图和水印，不用于裁切')
sources=json.loads((RUN/'sources.json').read_text('utf8'))
sources=[s for s in sources if s['id']!=item['id']]+[item]
(RUN/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf8')
e.detect(item)
d=json.loads((RUN/f'detections-v{e.VERSION}/2016-xdf.json').read_text('utf8'))
print([(q['number'],q['start']['page'],q['start']['bbox'],q['record_type']) for q in d['questions']])
