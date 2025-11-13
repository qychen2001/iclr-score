from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import StatisticsError, mean, median, pstdev
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


__all__ = [
    "PaperStats",
    "gather_paper_stats",
    "write_paper_stats_csv",
    "build_global_summary",
    "write_global_summary",
    "build_rating_histogram",
    "write_rating_histogram",
    "plot_rating_distribution_png",
    "load_primary_area_map",
    "BinRow",
    "build_bins",
    "fill_distribution",
    "plot_primary_area_distribution",
    "write_primary_area_markdown",
    "export_svgs_to_png",
]


def _ensure_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - optional import
        raise RuntimeError("matplotlib 未安装，请先 `pip install matplotlib`.") from exc
    return plt


def _ensure_cairosvg():
    try:
        import cairosvg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - optional import
        raise RuntimeError("cairosvg 未安装，请先 `pip install cairosvg`.") from exc
    return cairosvg


# --------------------------------------------------------------------------- #
# Rating statistics
# --------------------------------------------------------------------------- #


@dataclass
class PaperStats:
    forum_id: str
    review_count: int
    rating_min: float
    rating_max: float
    rating_mean: float
    rating_median: float
    rating_std: Optional[float]
    confidence_mean: float
    confidence_median: float
    confidence_std: Optional[float]
    rating_percentile: Optional[float] = None

    @property
    def rating_range(self) -> float:
        return self.rating_max - self.rating_min


def parse_numeric(value: object) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        head = value.split(":", 1)[0].strip()
        simplified = "".join(ch for ch in head if ch.isdigit() or ch in ".-")
        for candidate in (head, simplified):
            if not candidate:
                continue
            try:
                return float(candidate)
            except ValueError:
                continue
    return None


def iter_review_records(ratings_dir: Path) -> Iterator[Tuple[str, float, float]]:
    for path in sorted(ratings_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for note in data.get("notes", []):
            content: Dict[str, Dict[str, object]] = note.get("content", {})
            rating = parse_numeric(content.get("rating", {}).get("value"))
            confidence = parse_numeric(content.get("confidence", {}).get("value"))
            if rating is None or confidence is None:
                continue
            yield path.stem, rating, confidence


def build_paper_stats(
    records: Iterable[Tuple[str, float, float]],
) -> Tuple[List[PaperStats], List[float], List[float]]:
    grouped: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for forum_id, rating, confidence in records:
        grouped[forum_id].append((rating, confidence))

    paper_stats: List[PaperStats] = []
    all_ratings: List[float] = []
    all_confidences: List[float] = []

    for forum_id, entries in grouped.items():
        ratings = [entry[0] for entry in entries]
        confidences = [entry[1] for entry in entries]
        paper_stats.append(
            PaperStats(
                forum_id=forum_id,
                review_count=len(entries),
                rating_min=min(ratings),
                rating_max=max(ratings),
                rating_mean=mean(ratings),
                rating_median=median(ratings),
                rating_std=pstdev(ratings) if len(ratings) > 1 else None,
                confidence_mean=mean(confidences),
                confidence_median=median(confidences),
                confidence_std=pstdev(confidences) if len(confidences) > 1 else None,
            )
        )
        all_ratings.extend(ratings)
        all_confidences.extend(confidences)

    return paper_stats, all_ratings, all_confidences


def summarize_series(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "std": None,
        }
    payload = {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "median": median(values),
    }
    try:
        payload["std"] = pstdev(values)
    except StatisticsError:
        payload["std"] = None
    return payload


def assign_rating_percentiles(stats: List[PaperStats]) -> None:
    if not stats:
        return
    sorted_indices = sorted(range(len(stats)), key=lambda idx: stats[idx].rating_mean)
    total = len(stats)
    pointer = 0
    while pointer < total:
        end = pointer
        value = stats[sorted_indices[pointer]].rating_mean
        while end + 1 < total and stats[sorted_indices[end + 1]].rating_mean == value:
            end += 1
        avg_rank = (pointer + 1 + end + 1) / 2
        percentile = (avg_rank / total) * 100
        for offset in range(pointer, end + 1):
            stats[sorted_indices[offset]].rating_percentile = percentile
        pointer = end + 1


def pick_extremes(
    stats: List[PaperStats],
    *,
    limit: int = 10,
    reverse: bool = True,
    min_reviews: int = 3,
) -> List[Dict[str, float]]:
    pool = [item for item in stats if item.review_count >= min_reviews]
    pool.sort(key=lambda item: item.rating_mean, reverse=reverse)
    return [
        {
            "forum_id": item.forum_id,
            "review_count": item.review_count,
            "rating_mean": item.rating_mean,
            "rating_median": item.rating_median,
            "rating_std": item.rating_std,
            "confidence_mean": item.confidence_mean,
        }
        for item in pool[:limit]
    ]


def write_paper_stats_csv(stats: List[PaperStats], output_path: Path) -> None:
    fieldnames = [
        "forum_id",
        "review_count",
        "rating_min",
        "rating_max",
        "rating_range",
        "rating_mean",
        "rating_median",
        "rating_std",
        "rating_percentile",
        "confidence_mean",
        "confidence_median",
        "confidence_std",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for stat in stats:
            payload = {
                **asdict(stat),
                "rating_range": stat.rating_range,
            }
            writer.writerow(payload)


def build_global_summary(
    stats: List[PaperStats],
    all_ratings: List[float],
    all_confidences: List[float],
    *,
    ratings_dir: Path,
    min_reviews_for_extremes: int = 3,
) -> Dict[str, object]:
    reviews_per_paper = [item.review_count for item in stats]
    rating_histogram = build_histogram_series(all_ratings)
    confidence_histogram = build_histogram_series(all_confidences)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ratings_dir": str(ratings_dir),
        "total_papers": len(stats),
        "total_reviews": len(all_ratings),
        "reviews_per_paper": summarize_series(reviews_per_paper),
        "rating": {
            **summarize_series(all_ratings),
            "histogram": rating_histogram,
        },
        "confidence": {
            **summarize_series(all_confidences),
            "histogram": confidence_histogram,
        },
        "top_papers_by_avg_rating": pick_extremes(
            stats,
            limit=10,
            reverse=True,
            min_reviews=min_reviews_for_extremes,
        ),
        "bottom_papers_by_avg_rating": pick_extremes(
            stats,
            limit=10,
            reverse=False,
            min_reviews=min_reviews_for_extremes,
        ),
    }


def write_global_summary(summary: Dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Histogram + PNG
# --------------------------------------------------------------------------- #


def build_histogram_series(values: List[float]) -> Dict[str, int]:
    counter = Counter()
    for value in values:
        label = f"{value:.1f}" if not float(value).is_integer() else f"{int(value)}"
        counter[label] += 1
    return dict(sorted(counter.items(), key=lambda item: float(item[0])))


def build_rating_histogram(
    stats: List[PaperStats],
    *,
    bin_size: float = 1.0,
) -> Dict[str, object]:
    rating_means = [item.rating_mean for item in stats]
    histogram = build_histogram_bins(rating_means, bin_size)
    top_shares = compute_top_shares(rating_means)
    half_step_stats = compute_half_step_cumulative(rating_means, 0.5)
    return {
        "bin_size": bin_size,
        "total_papers": len(stats),
        "histogram": histogram,
        "score_top_percentiles": top_shares,
        "half_point_cumulative": {
            "step": 0.5,
            "points": half_step_stats,
        },
    }


def write_rating_histogram(
    payload: Dict[str, object],
    *,
    csv_output: Path,
    json_output: Path,
    stats: List[PaperStats],
) -> None:
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["forum_id,review_count,rating_mean\n"]
    for item in stats:
        lines.append(f"{item.forum_id},{item.review_count},{item.rating_mean:.4f}\n")
    csv_output.write_text("".join(lines), encoding="utf-8")

    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_histogram_bins(values: List[float], bin_size: float) -> Dict[str, int]:
    if not values:
        return {}
    start = math.floor(min(values) / bin_size) * bin_size
    end = math.ceil(max(values) / bin_size) * bin_size
    bucket_edges = []
    current = start
    while current <= end + 1e-9:
        bucket_edges.append(current)
        current = round(current + bin_size, 10)
    histogram = {
        f"{bucket_edges[i]:.1f}-{bucket_edges[i + 1]:.1f}": 0
        for i in range(len(bucket_edges) - 1)
    }
    for value in values:
        idx = min(int((value - start) // bin_size), len(bucket_edges) - 2)
        bucket_label = f"{bucket_edges[idx]:.1f}-{bucket_edges[idx + 1]:.1f}"
        histogram[bucket_label] += 1
    return histogram


def compute_top_shares(values: List[float]) -> List[Dict[str, float]]:
    if not values:
        return []
    sorted_values = sorted(values)
    total = len(sorted_values)
    result: List[Dict[str, float]] = []
    idx = 0
    while idx < total:
        value = sorted_values[idx]
        end = idx
        while end < total and sorted_values[end] == value:
            end += 1
        bottom_percent = (end / total) * 100
        top_percent = ((total - idx) / total) * 100
        result.append(
            {
                "score": value,
                "count": end - idx,
                "bottom_percent": bottom_percent,
                "top_percent": top_percent,
            }
        )
        idx = end
    return result


def compute_half_step_cumulative(
    values: List[float], step: float = 0.5
) -> List[Dict[str, float]]:
    if not values:
        return []
    sorted_values = sorted(values)
    total = len(sorted_values)
    min_score = math.floor(min(values) / step) * step
    max_score = math.ceil(max(values) / step) * step
    thresholds = []
    current = min_score
    while current <= max_score + 1e-9:
        thresholds.append(round(current, 4))
        current = round(current + step, 10)

    result: List[Dict[str, float]] = []
    for threshold in thresholds:
        count_le = sum(1 for value in sorted_values if value <= threshold)
        count_ge = sum(1 for value in sorted_values if value >= threshold)
        result.append(
            {
                "threshold": threshold,
                "bottom_percent": (count_le / total) * 100,
                "top_percent": (count_ge / total) * 100,
                "count_le_threshold": count_le,
                "count_ge_threshold": count_ge,
            }
        )
    return result


def parse_histogram_bars(
    data: Dict[str, object],
) -> List[Tuple[str, float, float, int]]:
    bins = []
    histogram = data.get("histogram", {})
    for label, count in histogram.items():
        start_str, end_str = label.split("-")
        start = float(start_str)
        end = float(end_str)
        bins.append((label, start, end, int(count)))
    bins.sort(key=lambda item: item[1])
    return bins


def parse_ahead_curve(data: Dict[str, object]) -> List[Tuple[float, float]]:
    cumulative_info = data.get("half_point_cumulative", {})
    points = cumulative_info.get("points", [])
    results: List[Tuple[float, float]] = []
    for item in points:
        threshold = float(item["threshold"])
        percent = float(item.get("top_percent") or 0.0)
        percent = max(0.0, percent)
        results.append((threshold, percent))
    results.sort(key=lambda item: item[0])
    return results


def plot_rating_distribution_png(
    histogram_payload: Dict[str, object],
    *,
    output_path: Path,
) -> None:
    plt = _ensure_matplotlib()
    bars = parse_histogram_bars(histogram_payload)
    ahead_curve = parse_ahead_curve(histogram_payload)
    if not bars:
        raise ValueError("Histogram data is empty.")

    bar_labels = [label for label, *_ in bars]
    bar_centers = [(start + end) / 2 for _, start, end, _ in bars]
    bar_widths = [end - start for _, start, end, _ in bars]
    bar_counts = [count for *_, count in bars]

    ahead_scores = [score for score, _ in ahead_curve]
    ahead_percent = [percent for _, percent in ahead_curve]

    fig, ax_count = plt.subplots(figsize=(12, 10))
    bars_plot = ax_count.bar(
        bar_centers,
        bar_counts,
        width=bar_widths,
        align="center",
        color="#4f78b7",
        alpha=0.85,
        edgecolor="#2c4674",
    )

    for rect, count in zip(bars_plot, bar_counts):
        ax_count.text(
            rect.get_x() + rect.get_width() / 2,
            rect.get_height() + max(bar_counts) * 0.01,
            f"{count}",
            ha="center",
            va="bottom",
            fontsize=14,
            color="#1b1b1b",
        )

    ax_count.set_xlabel("Average Score", fontsize=18)
    ax_count.set_ylabel("Paper Count", fontsize=18)

    max_labels = 12
    step = max(1, len(bar_labels) // max_labels)
    tick_indices = list(range(0, len(bar_labels), step))
    if tick_indices[-1] != len(bar_labels) - 1:
        tick_indices.append(len(bar_labels) - 1)
    tick_positions = [bar_centers[i] for i in tick_indices]
    tick_labels = [bar_labels[i] for i in tick_indices]
    ax_count.set_xticks(tick_positions)
    ax_count.set_xticklabels(
        tick_labels,
        rotation=90,
        ha="center",
        va="top",
        fontsize=16,
    )
    ax_count.tick_params(axis="y", labelsize=16)
    ax_count.grid(axis="y", linestyle="--", alpha=0.3)

    ax_ahead = ax_count.twinx()
    ax_ahead.plot(
        ahead_scores,
        ahead_percent,
        color="#e63946",
        marker="o",
        linewidth=2.5,
        markersize=7,
    )
    ax_ahead.set_ylabel("Ahead Percent", fontsize=18, color="#e63946")
    ax_ahead.tick_params(axis="y", labelsize=18, colors="#e63946")
    if ahead_percent:
        y_upper_limit = max(105, max(ahead_percent) + 8)
    else:
        y_upper_limit = 105
    ax_ahead.set_ylim(0, y_upper_limit)

    label_positions: List[Tuple[float, float]] = []
    min_vertical_gap = 4
    min_score_gap = 0.25
    base_offset = 3
    max_label_height = y_upper_limit - 1

    for score, percent in zip(ahead_scores, ahead_percent):
        label_y = percent + base_offset
        while any(
            abs(score - prev_score) < min_score_gap
            and abs(label_y - prev_y) < min_vertical_gap
            for prev_score, prev_y in label_positions
        ):
            label_y += min_vertical_gap
            if label_y >= max_label_height:
                break
        label_y = min(label_y, max_label_height)
        ax_ahead.text(
            score,
            label_y,
            f"{percent:.1f}%",
            ha="center",
            va="bottom",
            fontsize=13,
            color="#e63946",
        )
        label_positions.append((score, label_y))

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Primary area helpers
# --------------------------------------------------------------------------- #


def load_primary_area_map(
    submissions_path: Path, *, field_name: str = "primary_area"
) -> Dict[str, str]:
    if not submissions_path.exists():
        raise FileNotFoundError(f"{submissions_path} does not exist.")
    data = json.loads(submissions_path.read_text(encoding="utf-8"))
    mapping: Dict[str, str] = {}
    for note in data.get("notes", []):
        forum_id = note.get("forum")
        area = note.get("content", {}).get(field_name, {}).get("value")
        if forum_id and area:
            mapping[forum_id] = area
    return mapping


@dataclass
class BinRow:
    label: str
    count: int
    percent: float
    cumulative: float
    center: float
    start: float
    end: float


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value or "area"


def build_bins(
    min_score: float, max_score: float, bin_size: float
) -> List[Tuple[float, float]]:
    if bin_size <= 0:
        raise ValueError("bin_size must be greater than 0.")
    bins: List[Tuple[float, float]] = []
    current = min_score
    while current < max_score - 1e-9:
        upper = min(max_score, current + bin_size)
        bins.append((round(current, 2), round(upper, 2)))
        current = round(current + bin_size, 10)
    if bins:
        last_start, last_end = bins[-1]
        if not math.isclose(last_end, max_score, rel_tol=1e-9, abs_tol=1e-9):
            bins[-1] = (last_start, max_score)
    return bins


def fill_distribution(
    values: Sequence[float], bins: Sequence[Tuple[float, float]]
) -> List[BinRow]:
    counts = [0 for _ in bins]
    for value in values:
        for idx, (start, end) in enumerate(bins):
            right_open = idx < len(bins) - 1
            in_bin = start <= value < end if right_open else start <= value <= end
            if in_bin:
                counts[idx] += 1
                break
    total = len(values)
    rows: List[BinRow] = []
    cumulative = 0.0
    for idx, ((start, end), count) in enumerate(zip(bins, counts)):
        percent = (count / total * 100.0) if total else 0.0
        cumulative += percent
        if idx == len(bins) - 1 and total:
            cumulative = 100.0
        rows.append(
            BinRow(
                label=f"[{start:.1f}, {end:.1f})",
                count=count,
                percent=percent,
                cumulative=cumulative,
                center=(start + end) / 2.0,
                start=start,
                end=end,
            )
        )
    return rows


def plot_primary_area_distribution(
    area: str, total: int, rows: Sequence[BinRow], output_path: Path
) -> None:
    if not rows:
        return
    plt = _ensure_matplotlib()

    labels = [row.label for row in rows]
    counts = [row.count for row in rows]
    cumulative = [row.cumulative for row in rows]
    indices = list(range(len(rows)))

    fig, ax_count = plt.subplots(figsize=(12, 7))
    bars = ax_count.bar(
        indices,
        counts,
        color="#4f78b7",
        alpha=0.85,
        edgecolor="#2c4674",
        width=0.65,
    )

    max_count = max(counts) if counts else 0
    if max_count == 0:
        max_count = 1
    offset = max_count * 0.015 + 0.5
    for idx, (bar, count) in enumerate(zip(bars, counts)):
        if count == 0:
            continue
        ax_count.text(
            bar.get_x() + bar.get_width() / 2,
            count + offset,
            f"{count}",
            ha="center",
            va="bottom",
            fontsize=10,
            color="#1b1b1b",
        )

    ax_count.set_xticks(indices, labels)
    plt.setp(
        ax_count.get_xticklabels(), rotation=55, ha="right", rotation_mode="anchor"
    )
    ax_count.set_ylabel("Paper Count")
    ax_count.set_xlabel("Score Range")
    ax_count.set_title(f"{area} (N={total}) score distribution")
    ax_count.grid(axis="y", linestyle="--", alpha=0.3)

    ax_percent = ax_count.twinx()
    ax_percent.plot(
        indices,
        cumulative,
        color="#e63946",
        marker="o",
        linewidth=2.5,
        markersize=6,
    )
    for idx, value in zip(indices, cumulative):
        ax_percent.text(
            idx,
            value + 2,
            f"{value:.1f}%",
            color="#e63946",
            fontsize=10,
            ha="center",
            va="bottom",
        )
    upper_percent = min(110, max(105, max(cumulative) + 5))
    ax_percent.set_ylim(0, upper_percent)
    ax_percent.set_ylabel("Cumulative Share (%)", color="#e63946")
    ax_percent.tick_params(axis="y", colors="#e63946")

    fig.tight_layout()
    output_path = output_path.with_suffix(".png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_primary_area_markdown(
    output_path: Path,
    summary_rows: List[Tuple[str, int, float]],
    per_area_rows: List[Tuple[str, int, str, List[BinRow]]],
    plot_dir: Path,
) -> None:
    lines: List[str] = []
    lines.append("# Primary Area Score Distribution (0.5 bins)\n\n")
    lines.append("## Overview\n")
    lines.append("| Primary Area | Papers | Mean of Means |\n")
    lines.append("| --- | --- | --- |\n")
    for area, count, avg_mean in summary_rows:
        lines.append(f"| {area} | {count} | {avg_mean:.2f} |\n")
    lines.append("\n")

    rel_plot_dir = Path(os.path.relpath(plot_dir, output_path.parent))
    for area, count, slug, bin_rows in per_area_rows:
        lines.append(f"## {area} ({count} papers)\n")
        lines.append(f"![{area}](./{rel_plot_dir}/{slug}.png)\n")
        lines.append(
            "> Blue bars show counts per bin, red line shows cumulative percentage.\n\n"
        )
        lines.append("| Range (left-closed) | Count | Share % | Cumulative % |\n")
        lines.append("| --- | --- | --- | --- |\n")
        for row in bin_rows:
            lines.append(
                f"| {row.label} | {row.count} | {row.percent:.2f} | {row.cumulative:.2f} |\n"
            )
        lines.append("\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Misc utilities
# --------------------------------------------------------------------------- #


def gather_paper_stats(
    ratings_dir: Path,
) -> Tuple[List[PaperStats], List[float], List[float]]:
    paper_stats, all_ratings, all_confidences = build_paper_stats(
        iter_review_records(ratings_dir)
    )
    paper_stats.sort(key=lambda item: item.forum_id)
    assign_rating_percentiles(paper_stats)
    return paper_stats, all_ratings, all_confidences


def export_svgs_to_png(
    svg_paths: Iterable[Path],
    output_dir: Path,
    *,
    base_dir: Optional[Path] = None,
    scale: float = 1.0,
    overwrite: bool = False,
) -> Tuple[int, int]:
    cairosvg = _ensure_cairosvg()
    svg_list = [Path(path) for path in svg_paths]
    converted = 0
    skipped = 0
    if svg_list:
        base = base_dir or Path(
            os.path.commonpath([str(path.parent) for path in svg_list])
        )
    else:
        base = base_dir or output_dir
    for svg_path in svg_list:
        if base and svg_path.is_relative_to(base):
            rel_path = svg_path.relative_to(base)
        else:
            rel_path = Path(svg_path.name)
        png_path = (output_dir / rel_path).with_suffix(".png")
        png_path.parent.mkdir(parents=True, exist_ok=True)
        if png_path.exists() and not overwrite:
            skipped += 1
            continue
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=scale)
        converted += 1
    return converted, skipped
