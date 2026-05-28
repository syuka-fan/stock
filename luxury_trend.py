"""luxury_daily 종가를 티커별 MinMax(0~100) 스케일해 luxury_trend.json 생성.

각 luxury 티커의 전체 기간 Close 를 [0, 100] 으로 정규화하고,
사람이 읽는 이름(에르메스 등)을 1단 키, 날짜를 2단 키로 한 2중 dict 로 저장.
티커가 아니라 이름을 키로 쓰는 이유: 산출물을 사람이 바로 읽기 위함.

스케일은 티커별로 따로 적용 — luxury 종목은 통화(EUR/HKD)와 가격대가 달라
공통 스케일로 묶으면 비교가 무의미해지므로 종목 내부 진폭만 [0,100] 으로 환산한다.

사용법:
    python luxury_trend.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from common import (
    BASE_DIR,
    LUXURY_DIR,
    load_symbols_raw,
    reconfigure_stdio_utf8,
)

OUTPUT_PATH = BASE_DIR / "luxury_trend.json"

# 저장 자릿수. 거래일이 종목당 수천 개라 자릿수를 줄여 파일 크기를 억제한다.
ROUND_DIGITS = 2

# yfinance 가 상장 이전 비정상 저가(placeholder) 데이터를 돌려주는 종목의 상장일.
# 이 날짜(포함) 이전 행은 버린 뒤 남은 데이터로만 0~100 정규화한다.
# (안 버리면 상장 전 ~0 가격이 정규화에서 바닥에 깔려 평선으로 그려짐)
LISTING_START = {
    "1913.HK": "2011-06-24",  # 프라다 홍콩 상장
}


def read_luxury_close(path: Path) -> list[tuple[str, float]]:
    """원본 CSV 에서 (date, close) 추출.

    NaN/빈 Close 행은 스킵. 날짜 문자열(YYYY-MM-DD)은 사전식=시간식이라 그대로 정렬.
    """
    rows: list[tuple[str, float]] = []
    # utf-8-sig: 혹시 BOM 이 섞여도 첫 컬럼명("Date")이 깨지지 않도록 방어적으로 읽음.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            date = (r.get("Date") or "").strip()
            raw = (r.get("Close") or "").strip()
            if not date or raw == "" or raw.lower() == "nan":
                continue
            try:
                rows.append((date, float(raw)))
            except ValueError:
                continue
    rows.sort(key=lambda x: x[0])
    return rows


def scale_0_100(closes: list[float]) -> list[float]:
    """티커별 min/max 로 [0, 100] 스케일.

    span==0(전 구간 무변동)이면 0-나눗셈을 피해 전부 0.0 으로 둔다 (코드베이스 관례).
    """
    lo = min(closes)
    hi = max(closes)
    span = hi - lo
    if span == 0:
        return [0.0 for _ in closes]
    return [round((c - lo) / span * 100.0, ROUND_DIGITS) for c in closes]


def build_luxury_trend() -> dict[str, dict[str, float]]:
    luxury = load_symbols_raw().get("luxury", {})
    trend: dict[str, dict[str, float]] = {}
    for ticker, name in luxury.items():
        path = LUXURY_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"[경고] 누락: {path}")
            continue
        rows = read_luxury_close(path)
        start = LISTING_START.get(ticker)
        if start:
            # 날짜 문자열(YYYY-MM-DD)은 사전식=시간식이라 그대로 비교 가능.
            rows = [(d, c) for d, c in rows if d >= start]
        if not rows:
            print(f"[경고] 유효 Close 없음: {path}")
            continue
        dates = [d for d, _ in rows]
        scaled = scale_0_100([c for _, c in rows])
        trend[name] = dict(zip(dates, scaled))
        print(f"[OK] {ticker:8s} {name:6s} {len(rows):5d}일 "
              f"({dates[0]} ~ {dates[-1]})")
    return trend


def main() -> int:
    reconfigure_stdio_utf8()

    if not load_symbols_raw().get("luxury"):
        print("[오류] symbols.json 에 luxury 항목이 없습니다.")
        return 1

    trend = build_luxury_trend()
    if not trend:
        print("[오류] 생성할 luxury 데이터가 없습니다.")
        return 1

    OUTPUT_PATH.write_text(
        json.dumps(trend, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    total = sum(len(v) for v in trend.values())
    print(f"\n생성 완료: {OUTPUT_PATH}")
    print(f"  · 종목: {len(trend)}개 ({', '.join(trend)})")
    print(f"  · 총 날짜 포인트: {total:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
