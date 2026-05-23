"""한국/미국/지수 일봉 다운로더.

사용법:
    python download.py

동작:
- US_SYMBOLS 의 티커 → us_daily/<TICKER>.csv (yfinance)
- KR_SYMBOLS 의 티커 → kr_daily/<TICKER>.csv (FinanceDataReader)
- INDEX_SYMBOLS 의 지수 → index/<TICKER>.csv (지정된 소스)
- download_state.json 으로 티커별 last_date 관리. 없으면 1990-01-01 부터, 있으면 (last_date - 7일) 부터 받아 머지.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr

from common import BASE_DIR, INDEX_DIR, KR_DIR, US_DIR, load_symbols_raw


STATE_PATH = BASE_DIR / "download_state.json"

DEFAULT_START = "1990-01-01"
OVERLAP_DAYS = 7   # 제공사 사후 보정·지연 데이터 재흡수용 버퍼
SLEEP_SEC = 0.3    # yfinance 레이트리밋 회피 (낮추면 차단 위험)


def load_symbols() -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]:
    data = load_symbols_raw()
    us = dict(data["us"])
    kr = dict(data["kr"])
    index = {k: (v[0], v[1]) for k, v in data["index"].items()}
    return us, kr, index


def fetch_yf(ticker: str, start: str) -> pd.DataFrame | None:
    # auto_adjust=False: 원본 OHLC 보존, 분할/배당 보정은 머지 단계에서 처리
    # threads=False: yfinance 멀티스레드 시 간헐적 순서/누락 이슈 회피
    df = yf.download(
        ticker,
        start=start,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df


def fetch_fdr(ticker: str, start: str) -> pd.DataFrame | None:
    # ^KS11 -> 'KS11' (FDR 은 caret 없이 받음)
    sym = ticker.lstrip("^")
    df = fdr.DataReader(sym, start)
    if df is None or df.empty:
        return None
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[cols].dropna(how="all")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df


FETCHERS = {"yfinance": fetch_yf, "fdr": fetch_fdr}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[WARN] {STATE_PATH.name} 파싱 실패 — 빈 상태로 시작")
        return {}


def save_state(state: dict) -> None:
    # tmp → os.replace 로 원자적 교체: 쓰는 중 크래시해도 state 파일이 깨지지 않음
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def resume_start(state_entry: dict | None, out_path: Path) -> str:
    if not out_path.exists():
        return DEFAULT_START

    last_str: str | None = None
    if state_entry and "last_date" in state_entry:
        last_str = state_entry["last_date"]
    else:
        try:
            df = pd.read_csv(out_path, usecols=["Date"], parse_dates=["Date"])
            if not df.empty:
                last_str = df["Date"].max().strftime("%Y-%m-%d")
        except Exception:
            return DEFAULT_START

    if not last_str:
        return DEFAULT_START
    last = pd.Timestamp(last_str)
    return (last - pd.Timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")


def merge_with_existing(out_path: Path, new_df: pd.DataFrame) -> pd.DataFrame:
    if not out_path.exists():
        return new_df.sort_index()
    old = pd.read_csv(out_path, index_col="Date", parse_dates=True)
    combined = pd.concat([old, new_df])
    # Date 중복 시 새 값(분할/배당 보정 반영) 우선.
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.index.name = "Date"
    return combined


def process(
    ticker: str,
    desc: str,
    category: str,
    source: str,
    out_dir: Path,
    state: dict,
) -> tuple[str, str]:
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{ticker}.csv"
    start = resume_start(state.get(ticker), out_path)

    fetcher = FETCHERS[source]
    new_df = fetcher(ticker, start)

    if new_df is None or new_df.empty:
        if out_path.exists():
            return "unchanged", f"start={start} (응답 없음, 기존 유지)"
        return "empty", f"start={start} (응답 없음, 신규 실패)"

    merged = merge_with_existing(out_path, new_df)
    last_date = merged.index.max().strftime("%Y-%m-%d")
    rows_new = int(len(merged))

    # rows + last_date 가 이전 state 와 동일하면 데이터 변동 없음으로 간주 → CSV 재작성도 state 갱신도 생략.
    # WHY: to_csv 의 float repr 이 pandas 버전에 따라 미세하게 달라져 바이트 비교는 항상 'changed' 로 잡힘.
    #      메타 비교는 그 노이즈에 면역. 'rows 동일 + 값만 변경'(분할/배당 사후 보정) 케이스는 놓치지만
    #      auto_adjust=False + 일봉 특성상 실용상 무시 가능.
    # out_path.exists() 가드: 파일이 외부에서 삭제돼도 안전하게 재작성.
    prev = state.get(ticker, {})
    if (prev.get("last_date") == last_date
            and prev.get("rows") == rows_new
            and out_path.exists()):
        return "unchanged", f"rows={rows_new:5d} (변동 없음)"

    merged.to_csv(out_path, date_format="%Y-%m-%d")

    state[ticker] = {
        "description": desc,
        "category": category,
        "source": source,
        "out_path": str(out_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "last_date": last_date,
        "rows": rows_new,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    new_rows = len(new_df)
    return "ok", f"rows={rows_new:5d} (+{new_rows} fetched)  ~ {last_date}"


def build_jobs(
    us_symbols: dict[str, str],
    kr_symbols: dict[str, str],
    index_symbols: dict[str, tuple[str, str]],
) -> list[tuple[str, str, str, str, Path]]:
    jobs: list[tuple[str, str, str, str, Path]] = []
    for t, d in us_symbols.items():
        jobs.append((t, d, "us", "yfinance", US_DIR))
    for t, d in kr_symbols.items():
        jobs.append((t, d, "kr", "fdr", KR_DIR))
    for t, (d, src) in index_symbols.items():
        jobs.append((t, d, "index", src, INDEX_DIR))
    return jobs


def main() -> None:
    state = load_state()
    us_symbols, kr_symbols, index_symbols = load_symbols()
    jobs = build_jobs(us_symbols, kr_symbols, index_symbols)
    print(f"총 {len(jobs)} 티커 처리\n")

    counts = {"ok": 0, "unchanged": 0, "empty": 0, "fail": 0}
    for ticker, desc, category, source, out_dir in jobs:
        try:
            status, info = process(ticker, desc, category, source, out_dir, state)
            counts[status] = counts.get(status, 0) + 1
            print(f"[{status:9s}] {ticker:8s} {desc:25s} {info}")
        except Exception as e:
            counts["fail"] += 1
            print(f"[fail     ] {ticker:8s} {desc:25s} {type(e).__name__}: {e}")
        save_state(state)  # 장시간 실행 중 크래시/중단 시 진행분 보존
        time.sleep(SLEEP_SEC)

    print("\n요약:", " ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
