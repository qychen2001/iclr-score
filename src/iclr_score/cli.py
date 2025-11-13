from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is optional at runtime

    def tqdm(iterable, *_, **__):  # type: ignore
        return iterable


from .analytics import (
    BinRow,
    build_bins,
    build_global_summary,
    build_rating_histogram,
    fill_distribution,
    gather_paper_stats,
    load_primary_area_map,
    export_svgs_to_png,
    plot_primary_area_distribution,
    plot_rating_distribution_png,
    slugify,
    write_global_summary,
    write_paper_stats_csv,
    write_primary_area_markdown,
    write_rating_histogram,
)
from .openreview import OpenReviewClient, load_cookie_pairs, load_header_pairs

REQUIRED_REVIEW_FIELDS = ("rating", "confidence")


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


def resolve_access_token(value: Optional[str]) -> str:
    token = value or os.environ.get("OPENREVIEW_ACCESS_TOKEN")
    if not token:
        raise SystemExit(
            "未找到 access token。请通过 --access-token 参数或 OPENREVIEW_ACCESS_TOKEN 环境变量提供。"
        )
    return token.strip()


def build_client_from_args(args: argparse.Namespace) -> OpenReviewClient:
    return OpenReviewClient(
        resolve_access_token(args.access_token),
        base_url=args.base_url,
        headers=load_header_pairs(getattr(args, "header", []) or []),
        cookies=load_cookie_pairs(getattr(args, "cookie", []) or []),
        timeout=args.timeout,
    )


def load_forum_ids_from_submissions(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids: List[str] = []
    for note in data.get("notes", []):
        forum_id = note.get("forum")
        if forum_id:
            ids.append(str(forum_id))
    return ids


def load_forum_ids_from_file(path: Path) -> List[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def deduplicate_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def has_required_review_fields(note: Dict[str, object]) -> bool:
    content = note.get("content", {})
    if not isinstance(content, dict):
        return False
    for field in REQUIRED_REVIEW_FIELDS:
        field_data = content.get(field)
        if not isinstance(field_data, dict):
            return False
        if "value" not in field_data:
            return False
    return True


def extract_review_notes(notes: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return [note for note in notes if has_required_review_fields(note)]


# ---------------------------------------------------------------------------- #
# Sub-commands
# ---------------------------------------------------------------------------- #


def handle_fetch_submissions(args: argparse.Namespace) -> None:
    client = build_client_from_args(args)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    notes: List[Dict[str, object]] = []
    for idx, note in enumerate(
        client.iter_all_notes(
            domain=args.domain,
            limit=args.batch_size,
            details=args.details,
            content_venue_id=args.venue_id,
            invitation=args.invitation,
            trash=args.include_trash,
            sleep_seconds=args.sleep,
        ),
        start=1,
    ):
        notes.append(note)
        if args.limit and len(notes) >= args.limit:
            break
        if args.verbose and idx % args.progress_every == 0:
            print(f"[fetch-submissions] 已获取 {len(notes)} 篇投稿。")

    payload = {"count": len(notes), "notes": notes}
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已将 {len(notes)} 条投稿写入 {output_path}")


def handle_fetch_ratings(args: argparse.Namespace) -> None:
    sources: List[str] = []
    if args.submissions_json:
        subs_path = Path(args.submissions_json)
        sources.extend(load_forum_ids_from_submissions(subs_path))
    if args.paper_list:
        sources.extend(load_forum_ids_from_file(Path(args.paper_list)))
    if not sources:
        raise SystemExit("请至少提供 --submissions-json 或 --paper-list 之一。")

    forum_ids = deduplicate_preserve_order(sources)
    if args.limit:
        forum_ids = forum_ids[: args.limit]
    if not forum_ids:
        print("没有可用的 forum id。")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    client = build_client_from_args(args)

    def fetch_and_save(forum_id: str) -> Tuple[str, Optional[str]]:
        output_path = output_dir / f"{forum_id}.json"
        if output_path.exists() and not args.overwrite:
            return ("skipped", None)
        try:
            data = client.fetch_forum_reviews(
                forum_id,
                domain=args.domain,
                include_trash=args.include_trash,
                details=args.details,
            )
        except Exception as exc:  # noqa: BLE001
            return ("failed", str(exc))

        review_notes = extract_review_notes(data.get("notes", []))
        if not review_notes and not args.keep_empty:
            return ("empty", "no rating fields found")

        payload = {"count": len(review_notes), "notes": review_notes}
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ("saved", None)

    saved = skipped = failed = empty = 0
    statuses: List[Tuple[str, str]] = []

    if args.max_workers <= 1:
        iterator = forum_ids
        if args.show_progress:
            iterator = tqdm(forum_ids, desc="fetch-ratings")
        for forum_id in iterator:
            status, message = fetch_and_save(forum_id)
            if status == "saved":
                saved += 1
            elif status == "skipped":
                skipped += 1
            elif status == "empty":
                empty += 1
            else:
                failed += 1
                if message:
                    statuses.append((forum_id, message))
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(fetch_and_save, forum_id): forum_id
                for forum_id in forum_ids
            }
            iterator = as_completed(futures)
            if args.show_progress:
                iterator = tqdm(iterator, total=len(futures), desc="fetch-ratings")
            for future in iterator:
                forum_id = futures[future]
                status, message = future.result()
                if status == "saved":
                    saved += 1
                elif status == "skipped":
                    skipped += 1
                elif status == "empty":
                    empty += 1
                else:
                    failed += 1
                    if message:
                        statuses.append((forum_id, message))

    print(f"完成：保存 {saved}，跳过 {skipped}，空数据 {empty}，失败 {failed}。")
    if statuses:
        print("失败详情：")
        for forum_id, message in statuses[:20]:
            print(f" - {forum_id}: {message}")
        if len(statuses) > 20:
            print(f"  ... 其余 {len(statuses) - 20} 项已省略。")


def handle_analyze(args: argparse.Namespace) -> None:
    ratings_dir = Path(args.ratings_dir)
    stats, ratings, confidences = gather_paper_stats(ratings_dir)
    write_paper_stats_csv(stats, Path(args.csv_output))
    summary = build_global_summary(
        stats,
        ratings,
        confidences,
        ratings_dir=ratings_dir,
        min_reviews_for_extremes=args.min_reviews_for_extremes,
    )
    write_global_summary(summary, Path(args.summary_output))
    print(f"已写入 {args.csv_output} 与 {args.summary_output}")


def handle_histogram(args: argparse.Namespace) -> None:
    ratings_dir = Path(args.ratings_dir)
    stats, _, _ = gather_paper_stats(ratings_dir)
    payload = build_rating_histogram(stats, bin_size=args.bin_size)
    write_rating_histogram(
        payload,
        csv_output=Path(args.csv_output),
        json_output=Path(args.json_output),
        stats=stats,
    )
    print(f"已写入 {args.csv_output} 与 {args.json_output}")


def handle_primary_areas(args: argparse.Namespace) -> None:
    stats, _, _ = gather_paper_stats(Path(args.ratings_dir))
    area_map = load_primary_area_map(Path(args.submissions), field_name=args.field_name)

    bins = build_bins(args.min_score, args.max_score, args.bin_size)
    area_scores: Dict[str, List[float]] = defaultdict(list)
    for stat in stats:
        area = area_map.get(stat.forum_id)
        if not area:
            continue
        area_scores[area].append(stat.rating_mean)

    summary_rows: List[Tuple[str, int, float]] = []
    per_area_rows: List[Tuple[str, int, str, List[BinRow]]] = []
    plot_dir = Path(args.plot_dir)

    for area, scores in sorted(
        area_scores.items(), key=lambda item: len(item[1]), reverse=True
    ):
        rows = fill_distribution(scores, bins)
        slug = slugify(area)
        per_area_rows.append((area, len(scores), slug, rows))
        avg_mean = sum(scores) / len(scores) if scores else 0.0
        summary_rows.append((area, len(scores), avg_mean))
        plot_primary_area_distribution(area, len(scores), rows, plot_dir / slug)

    write_primary_area_markdown(
        Path(args.markdown_output), summary_rows, per_area_rows, plot_dir
    )
    print(f"表格写入 {args.markdown_output}，图像位于 {plot_dir}")


def handle_plot_rating(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.histogram_json).read_text(encoding="utf-8"))
    plot_rating_distribution_png(payload, output_path=Path(args.output))
    print(f"已写入 {args.output}")


def handle_export_svgs(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir or args.input_dir)
    svg_files = sorted(input_dir.rglob("*.svg"))
    if not svg_files:
        print(f"{input_dir} 中没有 SVG 文件。")
        return
    converted, skipped = export_svgs_to_png(
        svg_files,
        output_dir,
        base_dir=input_dir,
        scale=args.scale,
        overwrite=args.overwrite,
    )
    print(f"完成：转换 {converted}，跳过 {skipped}。")


# ---------------------------------------------------------------------------- #
# Parser
# ---------------------------------------------------------------------------- #


def add_common_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--access-token",
        help="OpenReview access token（默认读取 OPENREVIEW_ACCESS_TOKEN）。",
    )
    parser.add_argument(
        "--base-url",
        default="https://api2.openreview.net",
        help="OpenReview API 根地址。",
    )
    parser.add_argument("--timeout", type=int, default=30, help="请求超时时间（秒）。")
    parser.add_argument(
        "--cookie",
        action="append",
        help="需要附带的 Cookie（格式 key=value，可多次提供）。",
    )
    parser.add_argument(
        "--header",
        action="append",
        help="额外的 Header（格式 key=value，可多次提供）。",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ICLR score 工具集 CLI。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch-submissions
    fetch_sub = subparsers.add_parser("fetch-submissions", help="拉取投稿列表。")
    add_common_connection_args(fetch_sub)
    fetch_sub.add_argument(
        "--domain", default="ICLR.cc/2026/Conference", help="OpenReview domain。"
    )
    fetch_sub.add_argument(
        "--venue-id",
        default="ICLR.cc/2026/Conference/Submission",
        help="content.venueid 参数。",
    )
    fetch_sub.add_argument("--invitation", help="可选 invitation 参数。")
    fetch_sub.add_argument(
        "--details", default="replyCount,presentation,writable", help="details 字段。"
    )
    fetch_sub.add_argument(
        "--batch-size", type=int, default=1000, help="每批拉取数量。"
    )
    fetch_sub.add_argument(
        "--sleep", type=float, default=0.5, help="每批之间的休眠秒数。"
    )
    fetch_sub.add_argument(
        "--include-trash", action="store_true", help="是否包含 trash 数据。"
    )
    fetch_sub.add_argument("--limit", type=int, help="最多拉取多少条（可用于调试）。")
    fetch_sub.add_argument(
        "--output", default="data/submissions.json", help="输出路径。"
    )
    fetch_sub.add_argument("--verbose", action="store_true", help="打印进度。")
    fetch_sub.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="verbose 模式下每多少条打印一次。",
    )
    fetch_sub.set_defaults(func=handle_fetch_submissions)

    # fetch-ratings
    fetch_rate = subparsers.add_parser(
        "fetch-ratings", help="按 forum id 拉取评审 JSON。"
    )
    add_common_connection_args(fetch_rate)
    fetch_rate.add_argument(
        "--domain", default="ICLR.cc/2026/Conference", help="OpenReview domain。"
    )
    fetch_rate.add_argument(
        "--details",
        default="writable,signatures,invitation,presentation,tags",
        help="details 字段。",
    )
    fetch_rate.add_argument(
        "--submissions-json", help="包含投稿数据的 JSON，可用于提取 forum id。"
    )
    fetch_rate.add_argument("--paper-list", help="手动维护的 forum id 列表（逐行）。")
    fetch_rate.add_argument("--limit", type=int, help="限制请求的 forum 数量。")
    fetch_rate.add_argument("--output-dir", default="data/ratings", help="输出目录。")
    fetch_rate.add_argument(
        "--overwrite", action="store_true", help="若文件存在是否覆盖。"
    )
    fetch_rate.add_argument(
        "--keep-empty", action="store_true", help="即便没有评分字段也保存文件。"
    )
    fetch_rate.add_argument(
        "--include-trash", action="store_true", help="请求时包含 trash 数据。"
    )
    fetch_rate.add_argument(
        "--max-workers", type=int, default=8, help="并发线程数，1 表示串行。"
    )
    fetch_rate.add_argument(
        "--show-progress", action="store_true", help="显示 tqdm 进度条。"
    )
    fetch_rate.set_defaults(func=handle_fetch_ratings)

    # analyze
    analyze = subparsers.add_parser("analyze", help="汇总评分统计。")
    analyze.add_argument(
        "--ratings-dir", default="data/ratings", help="评审 JSON 目录。"
    )
    analyze.add_argument(
        "--csv-output", default="outputs/paper_rating_stats.csv", help="逐篇统计 CSV。"
    )
    analyze.add_argument(
        "--summary-output",
        default="outputs/global_summary.json",
        help="全局摘要 JSON。",
    )
    analyze.add_argument(
        "--min-reviews-for-extremes",
        type=int,
        default=3,
        help="top/bottom 榜单最少评审数。",
    )
    analyze.set_defaults(func=handle_analyze)

    # histogram
    histogram = subparsers.add_parser("histogram", help="生成均分直方图 JSON/CSV。")
    histogram.add_argument("--ratings-dir", default="data/ratings", help="评审目录。")
    histogram.add_argument("--bin-size", type=float, default=1.0, help="分箱大小。")
    histogram.add_argument(
        "--csv-output", default="outputs/paper_rating_means.csv", help="均分 CSV 输出。"
    )
    histogram.add_argument(
        "--json-output",
        default="outputs/rating_mean_histogram.json",
        help="直方图 JSON 输出。",
    )
    histogram.set_defaults(func=handle_histogram)

    # primary areas
    primary = subparsers.add_parser(
        "primary-areas", help="按 Primary Area 生成分布表与图。"
    )
    primary.add_argument(
        "--submissions", default="data/submissions.json", help="投稿 JSON。"
    )
    primary.add_argument("--ratings-dir", default="data/ratings", help="评分目录。")
    primary.add_argument(
        "--field-name", default="primary_area", help="投稿 JSON 中的字段名。"
    )
    primary.add_argument("--bin-size", type=float, default=0.5, help="分箱大小。")
    primary.add_argument("--min-score", type=float, default=0.0, help="最小评分。")
    primary.add_argument("--max-score", type=float, default=8.5, help="最大评分。")
    primary.add_argument(
        "--markdown-output",
        default="outputs/primary_area_tables.md",
        dest="markdown_output",
        help="Markdown 输出。",
    )
    primary.add_argument(
        "--plot-dir", default="outputs/primary_area_plots", help="图像输出目录。"
    )
    primary.set_defaults(func=handle_primary_areas)

    # plot rating distribution
    plot_rating = subparsers.add_parser(
        "plot-rating", help="根据直方图 JSON 生成 PNG。"
    )
    plot_rating.add_argument(
        "--histogram-json",
        default="outputs/rating_mean_histogram.json",
        help="直方图 JSON。",
    )
    plot_rating.add_argument(
        "--output", default="outputs/rating_distribution.png", help="PNG 输出路径。"
    )
    plot_rating.set_defaults(func=handle_plot_rating)

    # export svgs
    export_svg = subparsers.add_parser("export-svgs", help="批量将 SVG 导出为 PNG。")
    export_svg.add_argument(
        "--input-dir", default="outputs/primary_area_plots", help="SVG 目录。"
    )
    export_svg.add_argument("--output-dir", help="PNG 输出目录，默认与输入相同。")
    export_svg.add_argument("--scale", type=float, default=1.0, help="缩放倍率。")
    export_svg.add_argument(
        "--overwrite", action="store_true", help="若 PNG 已存在是否覆盖。"
    )
    export_svg.set_defaults(func=handle_export_svgs)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
