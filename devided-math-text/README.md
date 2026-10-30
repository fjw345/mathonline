# devided-math-text

由 MinerU 4.1.2 从 `devided-math/` 下的题目/答案图片转换得到的 **文字版**（Markdown + LaTeX）。

- 目录结构与图片仓库**一一对应**：
  `devided-math/北交大历年期末真题/微积分上/2016/题目/一1.png`
  → `devided-math-text/北交大历年期末真题/微积分上/2016/题目/一1.md`
- 公式用 `$...$`（行内）和 `$$...$$`（行间）
- 几何题的插图放在同级的 `<文件名>.assets/` 目录，markdown 里用相对路径引用
- 题目与答案的文件名一一对应（`题目/一1.md` ↔ `答案/一1.md`），
  分片答案（如 `七上/七下`）在 `_index/pairs.jsonl` 里有完整映射

## 统计

| 项 | 数量 |
|---|---|
| 文字版文件 | 4218（题目 1980 + 答案 2238） |
| 空文件 | 8 |
| 插图 | 65 张 |
| 可配对的题目 | 1815 / 1980 |

配对关系：`_index/pairs.jsonl`（每行 `{question, answers}`）

## 重新生成

```powershell
python E:\MinerU\scripts\export_to_repo.py
```

> 转换质量：公式可渲染率 99.8%；约 5.8% 的输出被质检标记（其中多数是源图本身就是单个符号的天然短答案）。
