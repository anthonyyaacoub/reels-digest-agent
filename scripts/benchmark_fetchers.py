"""Benchmark fetcher candidates against scripts/benchmark_urls.txt.

Usage:
    python -m scripts.benchmark_fetchers --fetcher playwright
    python -m scripts.benchmark_fetchers --fetcher scrapling

Writes per-fetcher results to data/benchmark_<fetcher>.json and prints a summary.
The underlying fetch_log.jsonl already records every call via log_fetch().
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def load_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def is_medium_family(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if "medium.com" in host:
        return True
    medium_pubs = ("plainenglish.io", "gitconnected.com", "betterprogramming.pub", "towardsdatascience.com")
    return any(p in host for p in medium_pubs)


def run_one(url: str, fetcher: str):
    t0 = time.monotonic()
    host = (urlparse(url).netloc or "").lower()
    try:
        if fetcher == "playwright":
            from src.generic_article_fetcher import fetch_generic_article
            article = fetch_generic_article(url)
        elif fetcher == "scrapling":
            from src.scrapling_fetcher import fetch_with_scrapling
            article = fetch_with_scrapling(url)
        else:
            raise ValueError(f"unknown fetcher: {fetcher}")
        body_chars = len(article.content_md)
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "url": url, "host": host, "fetcher": fetcher,
            "duration_ms": duration_ms, "body_chars": body_chars,
            "ok": body_chars >= 1500, "error": None,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "url": url, "host": host, "fetcher": fetcher,
            "duration_ms": duration_ms, "body_chars": 0,
            "ok": False, "error": repr(exc),
        }


def percentile(sorted_values: list[int], p: float) -> int:
    if not sorted_values:
        return 0
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def summarize(results: list[dict], fetcher: str) -> None:
    total = len(results)
    ok_results = [r for r in results if r["ok"]]
    success = len(ok_results)
    print(f"\n=== Summary: {fetcher} ===")
    print(f"Success: {success}/{total} ({(success * 100 // total) if total else 0}%)")

    durations = sorted(r["duration_ms"] for r in ok_results)
    if durations:
        print(f"p50: {percentile(durations, 0.50)}ms  "
              f"p95: {percentile(durations, 0.95)}ms  "
              f"max: {max(durations)}ms")

    # Split by Medium-family vs other
    non_medium = [r for r in results if not is_medium_family(r["url"])]
    nm_ok = [r for r in non_medium if r["ok"] and r["duration_ms"] < 2000]
    if non_medium:
        print(f"Non-Medium URLs: {len(nm_ok)}/{len(non_medium)} return ≥1500 chars in <2s")

    print("\nPer-URL:")
    for r in results:
        flag = "OK " if r["ok"] else "FAIL"
        err = f" — {r['error']}" if r["error"] else ""
        print(f"  [{flag}] {r['duration_ms']:>6}ms  {r['body_chars']:>6} chars  {r['url']}{err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetcher", choices=["playwright", "scrapling"], default="playwright")
    ap.add_argument("--urls", default="scripts/benchmark_urls.txt")
    ap.add_argument("--skip-medium", action="store_true",
                    help="Skip Medium-family URLs (use for Scrapling — cookie injection unsupported)")
    args = ap.parse_args()

    urls_path = REPO_ROOT / args.urls
    urls = load_urls(urls_path)
    if args.skip_medium:
        urls = [u for u in urls if not is_medium_family(u)]

    print(f"Fetcher: {args.fetcher}")
    print(f"URLs: {len(urls)}")

    results = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}", flush=True)
        result = run_one(url, args.fetcher)
        flag = "OK" if result["ok"] else "FAIL"
        err = f" — {result['error']}" if result["error"] else ""
        print(f"  → {flag}  {result['duration_ms']}ms  {result['body_chars']} chars{err}")
        results.append(result)

    summarize(results, args.fetcher)

    out = REPO_ROOT / "data" / f"benchmark_{args.fetcher}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved: {out}")


if __name__ == "__main__":
    main()
