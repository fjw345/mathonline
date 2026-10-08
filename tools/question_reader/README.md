# 题库阅读器

一个本地、只读的题库查看工具，用于浏览 `data/problem_bank.sqlite3` 中已经拆分的题目。它不会写入数据库，也不会修改 `resource/` 下的原始文件。

## 启动

在项目根目录 `D:\mathonline` 执行：

```powershell
python tools/question_reader/app.py
```

然后打开 <http://127.0.0.1:8765/>。如果端口被占用，可以指定其他端口：

```powershell
python tools/question_reader/app.py --port 8766
```

也可以显式指定只读数据库和资源目录：

```powershell
python tools/question_reader/app.py --db data/problem_bank.sqlite3 --resource-root resource
```

## 功能

- 查看题目总数、来源数、识别告警数和重复别名数。
- 按关键词、来源类型、年份、题型、难度、复核状态筛选。
- 分页浏览题目，并打开详情查看题干、选项、答案、解析、标签、页码和原始来源。
- 单独查看识别告警；当前数据库中的 `short_block` 告警不会被伪装成完整题目。
- 重复题保留为一条题目，详情中显示重复别名数量，来源关系仍来自数据库原记录。

## 只读边界

服务端使用 SQLite URI `mode=ro` 和 `PRAGMA query_only = ON`。来源文件接口只允许访问数据库登记、且位于 `resource/` 目录内的文件；没有编辑、删除、导入或写库 API。

## 当前数据基线

当前还原数据库包含 6,369 道题、141 个已纳入来源、6 个排除来源、986 条识别告警（来自 68 个文件）。北交大月考资源仍按数据库中的排除记录处理。
