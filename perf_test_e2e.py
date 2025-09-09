#!/usr/bin/env python3
"""
Performance test: measure end-to-end latency for Telegram and Exchange handling.

Sends a simulated BWEnews signal directly to HTTP intakes:
- Telegram Bot: POST /signal → returns send timing metrics
- Exchange Checker: POST /signal → measures HTTP handling time

Usage:
  python3 perf_test_e2e.py --runs 5 \
    --tele-url http://localhost:8013/signal \
    --exch-url http://localhost:8014/signal
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict

import aiohttp


def make_test_signal(idx: int) -> Dict:
    now7 = datetime.now(timezone(timedelta(hours=7))).isoformat()
    return {
        "event_id": f"perf_{int(time.time()*1000)}_{idx}",
        "ts_iso": now7,
        "source": "bwenews_test",
        "headline": "UPBIT LISTING: FLOCK added to KRW market",
        "url": "https://upbit.com/notice/5494",
        "event_type": "LISTING",
        "primary_entity": "FLOCK",
        "entities": ["UPBIT", "LISTING", "FLOCK"],
        "severity": 0.9,
        "direction": "BULL",
        "confidence": 0.9
    }


async def post_with_timing(session: aiohttp.ClientSession, url: str, payload: Dict) -> Dict:
    t0 = time.time()
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            txt = await resp.text()
            dt = (time.time() - t0) * 1000.0
            try:
                data = json.loads(txt)
            except Exception:
                data = {"raw": txt}
            return {"status": resp.status, "elapsed_ms": round(dt, 1), "data": data}
    except Exception as e:
        dt = (time.time() - t0) * 1000.0
        return {"status": 0, "elapsed_ms": round(dt, 1), "error": str(e)}


async def run_once(idx: int, tele_url: str, exch_url: str) -> Dict:
    payload = make_test_signal(idx)
    async with aiohttp.ClientSession() as session:
        # fire both in parallel
        tele_task = asyncio.create_task(post_with_timing(session, tele_url, payload))
        exch_task = asyncio.create_task(post_with_timing(session, exch_url, payload))
        tele_res, exch_res = await asyncio.gather(tele_task, exch_task)

    # Extract telegram internal metrics if available
    tele_metrics = tele_res.get("data", {}).get("metrics") if isinstance(tele_res.get("data"), dict) else None
    return {
        "tele": tele_res,
        "exch": exch_res,
        "tele_metrics": tele_metrics
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tele-url", default="http://localhost:8013/signal")
    parser.add_argument("--exch-url", default="http://localhost:8014/signal")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    results = []
    print(f"Running {args.runs} performance iterations...")
    for i in range(args.runs):
        res = await run_once(i + 1, args.tele_url, args.exch_url)
        results.append(res)
        tele_elapsed = res["tele"]["elapsed_ms"]
        exch_elapsed = res["exch"]["elapsed_ms"]
        tele_ok = res["tele"].get("status") == 200 and res["tele"].get("data", {}).get("ok") is True
        exch_ok = res["exch"].get("status") == 200 and res["exch"].get("data", {}).get("ok") is True
        print(f"#{i+1}: tele={tele_elapsed}ms ok={tele_ok} exch={exch_elapsed}ms ok={exch_ok}")
        if res.get("tele_metrics"):
            m = res["tele_metrics"]
            print(f"    tele_metrics: filter={m.get('filter_ms')}ms format={m.get('format_ms')}ms send={m.get('send_ms')}ms e2e={m.get('end_to_end_ms')}ms")

    # Aggregate
    def avg(vals):
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    tele_times = [r["tele"]["elapsed_ms"] for r in results]
    exch_times = [r["exch"]["elapsed_ms"] for r in results]
    e2e_times = [r["tele_metrics"]["end_to_end_ms"] for r in results if r.get("tele_metrics")]

    print("\nSummary:")
    print(f"  Telegram HTTP avg: {avg(tele_times)} ms | max: {round(max(tele_times),1)} ms")
    print(f"  Exchange HTTP avg: {avg(exch_times)} ms | max: {round(max(exch_times),1)} ms")
    if e2e_times:
        print(f"  Telegram e2e (bot) avg: {avg(e2e_times)} ms | max: {round(max(e2e_times),1)} ms")
    budget_ms = 5000
    worst = max(tele_times + exch_times + (e2e_times if e2e_times else []))
    status = "PASS" if worst <= budget_ms else "FAIL"
    print(f"  SLA 5s budget worst={round(worst,1)} ms → {status}")


if __name__ == "__main__":
    asyncio.run(main())


