"""한국/미국/지수 종가의 연내 누적 방향성(cumflow) 분석.

cumflow = 연도별 MinMax 정규화 후 연초 대비 누적 변화.
가격 레벨이 아닌 "연내 진행 방향"을 연도간 비교 가능한 스케일로 환산한 값.

두 출력은 서로 다른 질문에 답한다:
- direction_all/    : 전체 기간 (month, day_idx)별 중앙값 밴드 → 계절성 신호
- direction_recent/ : 최근 RECENT_YEARS년 → 최근 트렌드
                      티커별로 band 또는 lines 스키마 분기:
                        band  — 윈도우 시작 전부터 존재하던 종목 (통계 신뢰 가능)
                        lines — 윈도우 중간에 상장된 종목 (표본 부족 → 연도별 raw)

correlation_analysis.py와 동일 데이터 소스 (symbols.json + index/, kr_daily/, us_daily/).

사용법:
    python direction_analysis.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    BASE_DIR,
    DIR_ALL,
    DIR_RECENT,
    build_close_frame,
    load_symbol_names,
    reconfigure_stdio_utf8,
    safe_filename,
)


# 최근 트렌드 윈도우. 짧으면 노이즈에 흔들리고 길면 "최근" 의미가 희석된다.
# 5년은 경기 사이클 1~2회를 포함하는 경험적 합의점.
RECENT_YEARS = 5

# 윈도우 시작 이전부터 존재해야 band 모드 자격.
# RECENT_YEARS-1로 잡아 (month, day_idx) 그룹당 표본 ≥4를 보장 — median/MAD가 의미를 가지는 최소선.
BAND_MIN_YEARS = 4

# MAD를 가우시안 표준편차로 환산하면 약 0.6745σ. K=1.5 → 약 ±1σ 신뢰대역.
# 더 좁으면 노이즈에 흔들리고, 더 넓으면 시각적 정보가 사라진다.
BAND_K = 1.5


def compute_cumflow(frame: pd.DataFrame) -> pd.DataFrame:
    """연도별 MinMax → 연초 대비 누적 변화.

    cumflow[t] = scaled[t] - scaled[first_valid_day_in_year]
    (= diff().cumsum()과 동치, NaN 안전 버전)
    """
    frame = frame.sort_index()
    parts: list[pd.DataFrame] = []
    for year, group in frame.groupby(frame.index.year):
        mins = group.min(axis=0)
        maxs = group.max(axis=0)
        # 거래정지 등 연내 무변동 종목: span=0 → 분모 1.0으로 두면 scaled가 전구간 0이 되어
        # cumflow도 평평한 0 라인이 된다. 의도된 동작 (NaN 회피).
        spans = (maxs - mins).replace(0, 1.0)
        scaled = 2.0 * (group - mins) / spans - 1.0
        # 연초 결측 종목도 "첫 유효일=0"을 보장. 다운스트림 시각화의 전제.
        first_valid = scaled.bfill().iloc[0]
        part = scaled - first_valid
        # 불변식: 각 티커의 연내 첫 유효일 cumflow는 정확히 0이어야 한다.
        firsts = part.bfill().iloc[0].dropna()
        assert np.allclose(firsts, 0.0), f"{year} 연초 cumflow ≠ 0"
        parts.append(part)
    return pd.concat(parts).sort_index()


def label_axes(cumflow: pd.DataFrame) -> pd.DataFrame:
    """year/month/day_idx 컬럼 부여. day_idx는 (year, month) 내 거래일 순번."""
    df = cumflow.copy()
    df.insert(0, "year", df.index.year)
    df.insert(1, "month", df.index.month)
    df.insert(2, "day_idx", df.groupby(["year", "month"]).cumcount() + 1)
    return df


def _band_agg(sub: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """(month, day_idx) 그룹별 n_years/median/MAD 밴드 산출.

    median/MAD를 쓰는 이유: 액면분할·상폐 직전 등 이상치에 강건.
    mean/std는 단일 극단값이 밴드 전체를 왜곡한다.

    sub: ['year','month','day_idx', value_col] 컬럼 보유,
         value_col 기준 dropna 처리 완료 상태여야 함.
    """
    cols = ["month", "day_idx", "n_years", "median", "upper", "lower"]
    if sub.empty:
        return pd.DataFrame(columns=cols)
    g = sub.groupby(["month", "day_idx"])[value_col]
    median = g.median()
    mad = g.apply(lambda v: (v - v.median()).abs().median())
    return pd.DataFrame(
        {
            "n_years": g.count(),
            "median": median,
            "upper": median + BAND_K * mad,
            "lower": median - BAND_K * mad,
        }
    ).reset_index()


def aggregate_overall(labeled: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """(month, day_idx) → n_years, median, upper, lower."""
    sub = labeled[["year", "month", "day_idx", ticker]].dropna(subset=[ticker])
    return _band_agg(sub, ticker)


def aggregate_recent_band(recent_labeled: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """최근 윈도우에서 전체기간과 동일한 band 집계."""
    sub = recent_labeled[["year", "month", "day_idx", ticker]].dropna(subset=[ticker])
    return _band_agg(sub, ticker)


def aggregate_recent_lines(recent_labeled: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """연도별 raw cumflow long-form: year, month, day_idx, cumflow."""
    sub = recent_labeled[["year", "month", "day_idx", ticker]].dropna(subset=[ticker])
    return sub.rename(columns={ticker: "cumflow"}).reset_index(drop=True)


def qualifies_for_band(labeled: pd.DataFrame, ticker: str,
                       cutoff: pd.Timestamp) -> bool:
    """첫 유효 거래일이 cutoff 이전이면 band 모드 자격.

    윈도우 시작 이전부터 존재해야 (month, day_idx) 그룹의 표본 수가 안정적이다.
    윈도우 중간에 상장된 종목은 그룹별 표본이 들쭉날쭉해 band가 오해를 유발하므로
    lines 모드로 폴백한다.
    """
    sub = labeled[ticker].dropna()
    return not sub.empty and sub.index.min() <= cutoff


def main() -> None:
    reconfigure_stdio_utf8()

    index_names, kr_names, us_names = load_symbol_names()
    print(f"지수 {len(index_names)}개, 한국 {len(kr_names)}개, 미국 {len(us_names)}개")

    frame = build_close_frame(index_names, kr_names, us_names)
    print(f"통합 데이터 shape: {frame.shape}")
    print(f"기간: {frame.index.min().date()} ~ {frame.index.max().date()}")

    cumflow = compute_cumflow(frame)
    labeled = label_axes(cumflow)
    print(f"cumflow shape: {cumflow.shape}")
    print(f"연도별 거래일 수: "
          f"{dict(labeled.groupby('year').size())}")

    recent_cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(years=RECENT_YEARS)
    band_cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(years=BAND_MIN_YEARS)
    recent_labeled = labeled[labeled.index >= recent_cutoff]
    if recent_labeled.empty:
        print(f"[경고] 최근 컷오프 {recent_cutoff.date()} 이후 데이터 없음")
    else:
        print(f"최근 {RECENT_YEARS}년 컷오프: {recent_cutoff.date()} "
              f"(band 자격 컷오프: {band_cutoff.date()}) "
              f"(실제: {recent_labeled.index.min().date()} ~ "
              f"{recent_labeled.index.max().date()}, "
              f"연도 {sorted(set(recent_labeled['year']))})")

    for d in (DIR_ALL, DIR_RECENT):
        d.mkdir(parents=True, exist_ok=True)

    tickers = list(frame.columns)

    summary_rows: list[dict] = []

    for ticker in tickers:
        fname = safe_filename(ticker)

        overall = aggregate_overall(labeled, ticker)
        overall.to_csv(DIR_ALL / f"{fname}.csv",
                       index=False, encoding="utf-8-sig")

        if not overall.empty:
            idx_max = overall["median"].idxmax()
            idx_min = overall["median"].idxmin()
            summary_rows.append({
                "ticker": ticker,
                "n_points": len(overall),
                "n_years_max": int(overall["n_years"].max()),
                "n_years_min": int(overall["n_years"].min()),
                "median_final": float(overall["median"].iloc[-1]),
                "median_max": float(overall["median"].max()),
                "median_max_month": int(overall.loc[idx_max, "month"]),
                "median_max_day_idx": int(overall.loc[idx_max, "day_idx"]),
                "median_min": float(overall["median"].min()),
                "median_min_month": int(overall.loc[idx_min, "month"]),
                "median_min_day_idx": int(overall.loc[idx_min, "day_idx"]),
            })

        if recent_labeled.empty:
            recent = pd.DataFrame(columns=["year", "month", "day_idx", "cumflow"])
        elif qualifies_for_band(labeled, ticker, band_cutoff):
            recent = aggregate_recent_band(recent_labeled, ticker)
        else:
            recent = aggregate_recent_lines(recent_labeled, ticker)

        recent.to_csv(DIR_RECENT / f"{fname}.csv",
                      index=False, encoding="utf-8-sig")

    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        summary_df.to_csv(DIR_ALL / "_summary.csv",
                          index=False, encoding="utf-8-sig")
        print(f"\n요약: {(DIR_ALL / '_summary.csv').relative_to(BASE_DIR)} "
              f"({len(summary_df)} 티커)")

    print(f"\n완료. CSV: {len(tickers)} 티커 × 2 (all+recent)")


if __name__ == "__main__":
    main()
