<div align="center">

# iclr-score

用于抓取并分析 OpenReview（例如 ICLR）评分数据的命令行工具集。

</div>

## 特性

- **统一 CLI**：`python -m iclr_score ...` 或安装后直接用 `iclr-score`，包含投稿抓取、评分抓取、统计、绘图、Primary Area 分析等子命令。
- **零敏感信息**：不再硬编码 access token、cookie 或真实数据，所有凭据通过参数 / 环境变量提供，仓库内仅保留合成示例。
- **模块化实现**：`src/iclr_score/` 暴露了 OpenReview 客户端与统计/绘制函数，可单独在 notebook 或脚本中复用。
- **示例数据**：`examples/data/` 中提供了两个虚拟投稿与评审 JSON，方便快速测试流程。

## 快速上手

1. 安装依赖（推荐 Python 3.10+）：

   ```bash
   pip install -e .
   # 或者使用 uv
   uv pip install -e .
   ```

2. 提供 OpenReview 凭据（至少需要 access token）：

   ```bash
   export OPENREVIEW_ACCESS_TOKEN="替换为自己的 token"
   # 如果需要附带 Cookie / Header，可在命令里通过 --cookie / --header 传入
   ```

3. 最常用的工作流：

   ```bash
   # 1) 拉取投稿列表
   iclr-score fetch-submissions \
     --output data/submissions.json \
     --domain ICLR.cc/2026/Conference \
     --venue-id ICLR.cc/2026/Conference/Submission

   # 2) 根据投稿中的 forum id 拉取评分
   iclr-score fetch-ratings \
     --submissions-json data/submissions.json \
     --output-dir data/ratings \
     --max-workers 8 --show-progress

   # 3) 汇总逐篇统计与全局摘要
   iclr-score analyze \
     --ratings-dir data/ratings \
     --csv-output outputs/paper_rating_stats.csv \
     --summary-output outputs/global_summary.json

   # 4) 生成均分直方图 JSON/CSV
   iclr-score histogram \
     --ratings-dir data/ratings \
     --bin-size 0.5 \
     --json-output outputs/rating_mean_histogram.json

   # 5) 将直方图渲染为 PNG
   iclr-score plot-rating \
     --histogram-json outputs/rating_mean_histogram.json \
     --output outputs/rating_distribution.png

   # 6) Primary Area 视角的分布与图像
   iclr-score primary-areas \
     --submissions data/submissions.json \
     --ratings-dir data/ratings \
     --markdown-output outputs/primary_area_tables.md \
     --plot-dir outputs/primary_area_plots
   ```

> **提示**：所有输出路径默认写入 `data/` 或 `outputs/` 目录，这些路径已经在 `.gitignore` 中忽略，便于开源时保持仓库纯净。

## CLI 速查

| 命令 | 作用 | 关键参数 |
| --- | --- | --- |
| `fetch-submissions` | 迭代拉取给定 domain/venue 的投稿 JSON | `--domain`, `--venue-id`, `--invitation`, `--batch-size`, `--output` |
| `fetch-ratings` | 按 forum id 拉取评审并保存到目录 | `--submissions-json` / `--paper-list`, `--output-dir`, `--max-workers`, `--overwrite` |
| `analyze` | 生成逐篇 CSV 与全局 summary JSON | `--ratings-dir`, `--csv-output`, `--summary-output` |
| `histogram` | 输出均分 CSV + 直方图 JSON | `--ratings-dir`, `--bin-size`, `--csv-output`, `--json-output` |
| `plot-rating` | 把直方图 JSON 渲染成 PNG | `--histogram-json`, `--output` |
| `primary-areas` | 依据投稿中的 primary area 绘制曲线与 Markdown | `--submissions`, `--field-name`, `--bin-size`, `--plot-dir` |
| `export-svgs` | 批量将某目录下的 SVG 转成 PNG | `--input-dir`, `--output-dir`, `--scale`, `--overwrite` |

所有需要访问 OpenReview 的命令都支持以下连接参数：

- `--access-token`：显式传入 token（缺省时读取 `OPENREVIEW_ACCESS_TOKEN` 环境变量）。
- `--cookie key=value`：若需要附带 Cookie，可多次提供。
- `--header key=value`：额外 header（例如自定义 `user-agent`）。
- `--base-url` / `--timeout`：可指向自建 proxy 或调整请求超时。

## 示例数据

`examples/data/` 下包含：

- `sample_submissions.json`：两个虚构投稿及其 `primary_area` 字段。
- `sample_ratings/`：对应 forum 的评审 JSON，字段结构与真实 OpenReview 响应一致。

可用这些文件验证 CLI：

```bash
iclr-score analyze --ratings-dir examples/data/sample_ratings --csv-output outputs/demo_stats.csv
iclr-score histogram --ratings-dir examples/data/sample_ratings --json-output outputs/demo_hist.json
```

## 隐私与合规

- 仓库中不再存放任何真实投稿、评审或个人 access token。
- `.gitignore` 已默认忽略 `data/`、`analysis/`、`ratings/` 等目录，避免误提交敏感输出。

## 目录结构概览

```
iclr-score/
├─ src/iclr_score/      # 核心实现（OpenReview 客户端、统计与 CLI）
├─ examples/data/       # 虚构示例输入
├─ README.md
├─ pyproject.toml
└─ uv.lock
```

欢迎根据需要扩展更多命令或自定义分析逻辑，`iclr_score` 模块中的函数都可以在 notebook/脚本里直接复用。
