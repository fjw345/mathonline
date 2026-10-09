"""Coverage, preservation, reference integrity and a review queue for this batch."""
import csv
import hashlib
import json
import math
from collections import Counter,defaultdict
from pathlib import Path
import fitz
from build_image_bank_v2 import RUN,ROOT,write


def main():
    manifest=json.loads((RUN/'manifest.json').read_text('utf-8'))
    report=json.loads((RUN/'report.json').read_text('utf-8'))
    catalog=json.loads((RUN/'catalog.json').read_text('utf-8'))
    before=json.loads((RUN/'before.json').read_text('utf-8'))
    edge=json.loads((RUN/'edge_repair_report.json').read_text('utf-8')) if (RUN/'edge_repair_report.json').exists() else {}
    source_gaps=json.loads((RUN/'source_sequence_findings.json').read_text('utf-8')) if (RUN/'source_sequence_findings.json').exists() else []
    protected_changed=[p for p,h in before['protected_hashes'].items() if not (ROOT/p).is_file() or hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    disappeared=[p for p in before['existing_files'] if not (ROOT/p).exists()]
    records=manifest['questions']+manifest['answer_records'];pages={p['id']:p for p in manifest['pages']};docs={d['id']:d for d in manifest['documents']}
    missing_images=[];invalid_refs=[];risks=[];seen_ids=set()
    for q in records:
        if q['id'] in seen_ids:invalid_refs.append(dict(id=q['id'],type='duplicate_id'))
        seen_ids.add(q['id'])
        for reg in q['regions']:
            if reg['page_id'] not in pages:invalid_refs.append(dict(id=q['id'],type='unknown_page'))
            b=reg.get('bbox',[])
            if len(b)!=4 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=1 for x in b) or b[0]>=b[2] or b[1]>=b[3]:invalid_refs.append(dict(id=q['id'],type='invalid_bbox'))
            if not reg.get('image') or not (RUN/reg['image']).is_file():missing_images.append(dict(id=q['id'],image=reg.get('image')))
        for role,path in q.get('files',{}).items():
            if not (RUN/path).is_file():missing_images.append(dict(id=q['id'],image=path))
        if q.get('suspected_answer_in_stem'):risks.append(dict(id=q['id'],type='answer_visible_in_stem',document_id=q['document_id']))
        if q.get('cross_reference',{}).get('status')=='unresolved':risks.append(dict(id=q['id'],type='unresolved_cross_reference',document_id=q['document_id']))
        stem_pages={r['page_id'] for r in q['regions'] if r['role']=='stem'}
        if len(stem_pages)>2:risks.append(dict(id=q['id'],type='long_stem_check_missing_boundary',document_id=q['document_id'],pages=len(stem_pages)))
        if q.get('answer_status')=='ambiguous':risks.append(dict(id=q['id'],type='ambiguous_answer',document_id=q['document_id']))
    # Every unlocated page is an explicitly named source-page image, never a
    # purported complete question. This also includes covers for transparent accounting.
    pending=[];openpdf={}
    for p in manifest['pages']:
        if p.get('status')!='unresolved':continue
        did=p['document_id']
        if did not in openpdf:openpdf[did]=fitz.open(ROOT/docs[did]['path'])
        target=RUN/'unresolved_pages'/f"{p['id']}.png";target.parent.mkdir(exist_ok=True)
        if not target.exists():openpdf[did][p['number']-1].get_pixmap(matrix=fitz.Matrix(1.8,1.8),alpha=False).save(target)
        pending.append(dict(page_id=p['id'],document_id=did,page=p['number'],source=docs[did]['original_path'],
                            image=target.relative_to(RUN).as_posix(),status='unsegmented_source_page',note='未定位题号；可能是封面、目录、答案表或待补切内容。不计入题数。'))
    for p in openpdf.values():p.close()
    duplicates=defaultdict(list)
    for d in manifest['documents']:duplicates[d['original_sha256']].append(d['id'])
    duplicates=[ids for ids in duplicates.values() if len(ids)>1]
    for q in records:
        for candidate in q.get('answer_candidates',[]):
            if candidate['record_id'] not in seen_ids:invalid_refs.append(dict(id=q['id'],type='unknown_answer_candidate'))
    page_status=dict(Counter(p.get('status','unknown') for p in manifest['pages']))
    edge_pending=[c for c in edge.get('changes',[]) if c.get('edge_contact_remaining')]
    audit=dict(existing_files_deleted=disappeared,protected_files_changed=protected_changed,missing_images=missing_images,
               invalid_references=invalid_refs,records=len(records),image_files=sum(1 for _ in (RUN/'images').rglob('*.png')),
               duplicate_source_groups=duplicates,risk_counts=dict(Counter(r['type'] for r in risks)),pending_pages=len(pending),page_status=page_status,
               edge_regions_repaired=edge.get('automatic_regions',0),edge_contact_pending=len(edge_pending))
    audit['source_documents_with_missing_questions']=len(source_gaps)
    write(RUN/'validation.json',audit);write(RUN/'review_queue.json',dict(pages=pending,records=risks,edge_contact=edge_pending,source_missing_questions=source_gaps))
    with (RUN/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['来源文件','状态','页数','题目记录','独立答案记录','未匹配答案','未定位页面'])
        for d in catalog:writer.writerow([d['source'],d['status'],d['pages'],d['questions'],d['answers'],d['unmatched'],';'.join(map(str,d['unresolved_pages']))])
    status=report['answer_status']
    text=f'''# resource 全量题图提取记录（2026-10-08）

本批次输出目录：`data/image_bank/runs/20261008-full-v2`。

## 实际结果

- 已处理 {report['documents_processed']} / {report['documents_total']} 份来源文件，共 {report['pages']} 页（包含 Word 转换页）。月考、期末、考研和竞赛资料均纳入。
- 生成 {report['question_drafts']} 条题目初切记录、{report['answer_records']} 条独立答案记录；这是按来源计数，包含重复资料和复述题干，不代表去重后的题库题数。
- 保存 {audit['image_files']} 张 PNG，题干、答案和解析分开。跨页片段按顺序保留，并提供拼接预览。
- 导入原测试 {report['manual_imported']} 题，其中 {report['manual_confirmed']} 题继承原人工边界确认；扫描数一第 10 题保留第 4 页补片，仍待复核。
- 同题原文内发现答案/解析 {status.get('inline_candidate',0)} 条；按考试身份关联答案 {status.get('matched_identity_pending_review',0)} 条；答案多候选 {status.get('ambiguous',0)} 条；尚未匹配 {status.get('unmatched',0)} 条。
- 关联“同数学某卷第某题”题干引用 {report.get('cross_references_linked',0)} 条。
- 自动边缘检查调整 {edge.get('automatic_regions',0)} 个区域，涉及 {edge.get('affected_records',0)} 条记录；从原页补出图像，保留修正前 PNG。人工试切题干不参与自动扩框。

## 怎样打开和调整

1. 双击项目根目录的 `打开全量切题工作台.cmd`，或运行 `python -B -X utf8 tools/image_bank_workbench/app.py`。
2. 浏览器访问 http://127.0.0.1:8767/ ，搜索文件名，然后选“看题图”或“调整边框”。
3. 调整方式沿用原工作台：拖边框、拖整框、补画片段、排序、改为答案/解析。可切换到配套答案文件补片。
4. 修改会保存在本批次对应 `workspaces/<来源ID>/annotations.json`，每次保存保留 `history/` 历史版本。原 `data/cropper_trial` 不变。
5. 初始图集是本轮快照。调整后点击“导出已确认题目”生成更新后的图片和坐标 ZIP，原始初切图仍保留。

## 需要继续复查的内容

这是全量自动初切及关联草稿，不能等同于全部题目已经人工核验或可直接发布。

- {len(pending)} 页没有可靠题号定位，已另存 `unresolved_pages/` 和 `review_queue.json`，可能包含封面、目录、答案表及待补切内容；这些页面不冒充一道题。
- 已经视觉区分封面 {page_status.get('cover',0)} 页、空白或只有页眉 {page_status.get('blank_or_header_only',0)} 页、广告 {page_status.get('advertisement',0)} 页，均保留原文件，不计为题目。
- {len(edge_pending)} 个自动区域扩边后仍检测到边缘墨迹，可能来自公式、表格边线或水印，已列入待核对队列。
- {audit['risk_counts'].get('long_stem_check_missing_boundary',0)} 条题干跨越 2 页以上，优先检查是否漏掉中间题号。
- {audit['risk_counts'].get('answer_visible_in_stem',0)} 条题目原文可能带正确答案，保留原像素，尚未遮盖。
- {audit['risk_counts'].get('unresolved_cross_reference',0)} 条“同某题”引用未唯一确定完整题干。
- 对单一考试身份、2004 年以后考研卷进行了题号连续性检查并修正漏定位。仍有两份原文件自身缺题：2012 数三 Word 止于第 21 题，缺第 22、23 题；2013 数三 Word 止于第 22 题，缺第 23 题。详见 `source_sequence_findings.json`，没有生成替代题目。
- 答案关联使用考试类别、年份/学期、科目/卷别、分组、场次、题型/大题和题号；尚未逐题证明内容对应和答案正确，多候选不自动选定。
- 扫描件识别只用于定位。题号序列和公式边界仍有误切、漏切或把小问当大题的可能。Word 的公式已抽查，未逐页与 Word 软件对照。
- `ocr_missing` 是定位引擎的回退标记，不能直接解释成整页丢失：27 页初始样本中，6 页为空白、1 页只有标题，其余为稀疏文字或以公式为主的页面，仍使用原生位置和原页图片。检查截图见 `review/low-text-sheet-*.png`。

## 检查与保留

- 图片缺失 {len(missing_images)} 项；无效来源引用 {len(invalid_refs)} 项；导出错误 {len(report['export_errors'])} 项。
- 本次操作前存在的工作区文件缺失 {len(disappeared)} 个；resource 和原试切 JSON 的哈希变化 {len(protected_changed)} 个。
- `manifest.json` 保留原文件哈希、转换后 PDF 哈希、页码、归一化坐标、角色、片段顺序及图片路径。
- `coverage.csv` 为逐文件覆盖表；`validation.json` 为检查证据；`review_queue.json` 为待核对队列。
- 原文件、旧批次、旧坐标、旧数据库均保留。生成目录已加入本仓库本地 Git 排除清单，避免新增大量题图挤满 Git 变更列表；不影响磁盘文件。
- 后端隔离工作区检查覆盖 Word 转换来源、跨文件答案、保存历史、版本冲突、坐标校验及 ZIP 导出，结果见 `backend_verification.json`。浏览器检查结果另记在 `browser_verification.json`（生成后可查）。
'''
    (RUN/'REPORT.md').write_text(text,encoding='utf-8')
    (ROOT/'全量切题执行记录-2026-10-08.md').write_text(text,encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))
    if protected_changed or disappeared or missing_images or invalid_refs:raise SystemExit('Validation requires attention')


if __name__=='__main__':main()
