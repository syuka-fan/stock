"""한국/미국/지수 종가 상관계수 분석.

데이터 소스
    ``symbols.json`` 기준으로 ``index/``, ``kr_daily/``, ``us_daily/`` 의 종가(Close)를 outer join.
    공통 로딩 로직은 ``common.build_close_frame`` 참조.

처리
    컬럼별 MinMax 정규화 후 Pearson 상관계수(pairwise NaN 무시).
    정규화는 '값의 크기가 아닌 방향성 중심' 으로 해석하려는 분석 의도를 코드에 남긴 것 —
    자세한 내용은 ``scale_frame`` docstring 참조.

출력
    * ``corr_all/``    : 전체 기간       overall.csv + 01.csv ~ 12.csv (월별)
    * ``corr_recent/`` : 최근 RECENT_YEARS 년 overall.csv + 01.csv ~ 12.csv (월별)
    파일은 utf-8-sig(BOM) 인코딩으로 저장 — Excel 호환 목적이므로 utf-8 로 바꾸지 말 것.

소비자
    출력 CSV 의 경로 상수(CORR_ALL_DIR / CORR_RECENT_DIR)는 ``common.py`` 에 정의되며,
    이를 import 하는 다른 스크립트/노트북이 결과를 읽는다.
    컬럼/종목 추가·제거 시 해당 소비자 영향도 함께 검토할 것.

멱등성
    ``corr_recent/`` 의 컷오프와 상장기간 필터(MIN_LISTING_YEARS)가 모두 실행일(today) 기준이라
    실행한 날짜에 따라 결과(특히 경계에 걸친 신규 종목 포함 여부)가 달라진다.
    같은 분석을 재현하려면 실행 날짜를 같이 기록해 둘 것.

자주 조정하는 노브
    * COLUMN_ORDER      : 행/열 순서 (의도된 그룹핑 있음 — 헤더 주석 참조)
    * RECENT_YEARS      : '최근' 기간 길이
    * MIN_LISTING_YEARS : 상장(데이터 시작) 후 이 기간 미만이면 종목 자체를 제외
    * scale_frame       : 정규화 방식

사용법:
    python correlation_analysis.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    BASE_DIR,
    CORR_ALL_DIR,
    CORR_RECENT_DIR,
    build_close_frame,
    load_symbol_names,
    reconfigure_stdio_utf8,
)


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────
# 최근 N년만 잘라 corr_recent/ 에 저장.
# WHY: 너무 먼 과거의 영향을 줄이고 '최근 시장 흐름' 의 상관관계를 분리해서 보기 위함.
#      임의 값이 아니라 분석 의도가 들어 있는 값 — 바꿀 때는 의도(최근 기간 정의)를 다시 합의할 것.
RECENT_YEARS = 3

# 상장(=데이터 시작) 후 N년이 안 된 종목은 상관계수에서 제외.
# WHY: 데이터 기간이 짧으면 표본이 부족해 상관계수 신뢰도가 낮음.
# 주의: 상장일 정보가 없어 'CSV 첫 거래일' 을 상장일 대용치로 사용. 신규 상장 구간에선 정확하고,
#       오래된 종목은 데이터 소스 시작일 한계가 있으나 어차피 기준을 넘으므로 영향 없음.
# RECENT_YEARS 와 값이 같아도 의미가 다르다(최근 분석창 길이 vs 최소 상장기간) — 별도 상수로 둠.
MIN_LISTING_YEARS = 3


# ─────────────────────────────────────────────────────────────────────────────
# 컬럼 순서
#
# 그룹핑 의도:
#   지역(한국 → 아시아 → 미국) × 자산군(지수 → 개별주 → ETF) × 섹터(반도체 → 빅테크 → 원자재)
#   순서로 묶어 두었다. 상관계수 행렬을 시각화했을 때 블록 대각 구조가 드러나서
#   '같은 그룹끼리 얼마나 묶이는가' 가 한눈에 보이도록 한 배치다.
#
#   → 그룹 경계를 무시하고 종목을 끼워 넣으면 위 시각적 해석이 깨진다.
#     순서를 바꿀 때는 가능하면 같은 섹션(── ... ──) 안에서만 이동할 것.
#
# 수정법:
#   - 빼고 싶은 종목: 해당 줄 삭제 또는 주석 처리
#   - 순서 바꾸기: 줄 위치 이동 (가급적 같은 섹션 내)
#   - 새 종목 추가: ``[IDX|KR|US] 종목명`` 형태로 적절한 섹션에 삽입
#   - 리스트에 없는데 데이터에 있는 컬럼은 자동으로 끝에 붙고 알림이 출력된다
#   - 리스트에 있는데 데이터에 없는 컬럼은 무시되고 알림이 출력된다
# ─────────────────────────────────────────────────────────────────────────────
COLUMN_ORDER: list[str] = [
    # ── 한국 지수 ────────────────────────────────
    "[IDX] KOSPI",
    "[IDX] KOSDAQ",
    "[US] 한국 추종 ETF",
    "[KR] 코스피200롱150숏선물"
    # ── 아시아 지수 ──────────────────────────────
    "[IDX] 닛케이225",
    "[IDX] 항셍",
    "[IDX] 상하이종합",
    "[IDX] 대만가권",
    # ── 한국 개별주 ──────────────────────────────
    "[KR] 삼성전자",
    "[KR] SK하이닉스",
    "[KR] 현대차",
    "[KR] 두산에너빌리티",
    "[KR] 현대로템",
    "[KR] 네이버",
    "[KR] 카카오",
    "[KR] LG전자",
    "[KR] 에이피알",
    # ── 한국 ETF ────────────────────────────────
    "[KR] KODEX 조선TOP10",
    "[KR] KODEX AI전력핵심설비",
    "[KR] KODEX 2차전지산업",
    "[KR] KODEX 자동차",
    "[KR] TIGER 200 건설",
    "[KR] TIGER 의료기기",
    "[KR] TIGER 반도체",
    "[KR] TIGER 증권",
    "[KR] TIGER 헬스케어",
    "[KR] TIGER 은행",
    # ── 한국 금융지주 ──
    "[KR] 신한지주",
    "[KR] 하나금융지주",
    "[KR] KB금융",
    "[KR] 우리금융지주",
    # ── 미국 지수 및 ETF ────────────────────────────────
    "[IDX] 다우 존스",
    "[IDX] S&P 500",
    "[US] S&P500",
    "[IDX] 러셀 2000",
    "[US] 러셀",
    "[US] 미국 전체",
    "[IDX] NASDAQ",
    "[US] 나스닥",
    "[US] 나스닥 레버리지",
    "[US] 미국 반도체",
    # ── 미국 반도체 ──────────────────────────────
    "[US] 엔비디아",
    "[US] 마이크론",
    "[US] 브로드컴",
    "[US] 인텔",
    "[US] AMD",
    "[US] 퀄컴",
    "[US] 샌디스크",
    "[US] 시게이트",
    "[US] 웨스턴디지털",
    # ── 미국 빅테크/개별 ─────────────────────────
    "[US] 애플",
    "[US] 마이크로소프트",
    "[US] 구글",
    "[US] 소프트웨어 ETF",
    "[US] 테슬라",
    "[US] 월마트",
    "[US] 코카콜라",
    "[US] 제타",
    "[US] 이즈코프",
    "[US] 이머징 마켓",
    "[US] 선진국 마켓",
    "[US] 항공우주 ETF",
    # ── 원자재 ────────────────────────────────────
    "[US] 금",
    "[US] 은",
    "[US] 미국 오일 펀드",
    "[US] 원자재 펀드"
]


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def reorder_columns(frame: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """frame 의 컬럼을 ``order`` 기준으로 재정렬.

    부수효과: 매칭이 안 되는 컬럼이 있으면 stdout 에 알림을 찍는다 (순수 함수가 아님).
    cron/배치 로그를 보는 사람이 정의 불일치를 빨리 발견하라고 일부러 print 로 남겨둔다.
    """
    available = [c for c in order if c in frame.columns]
    leftovers = [c for c in frame.columns if c not in available]
    if leftovers:
        print(f"[알림] COLUMN_ORDER에 없어 끝에 붙는 컬럼: {leftovers}")
    missing = [c for c in order if c not in frame.columns]
    if missing:
        print(f"[알림] COLUMN_ORDER에 있지만 데이터에 없는 컬럼(무시됨): {missing}")
    return frame[available + leftovers]


def filter_by_listing(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """첫 거래일이 ``cutoff`` 이후인(=상장 기간이 짧은) 컬럼을 제외.

    상장일 대용치로 컬럼별 첫 유효(non-NaN) 날짜를 사용 — 자세한 배경은 MIN_LISTING_YEARS 주석 참조.
    부수효과: 제외된 종목을 stdout 에 알림 (reorder_columns 와 동일 정책 — 배치 로그에서 빨리 확인).
    """
    keep, dropped = [], []
    for col in frame.columns:
        first = frame[col].first_valid_index()
        if first is not None and first <= cutoff:
            keep.append(col)
        else:
            dropped.append((col, first))
    for col, first in dropped:
        when = first.date() if first is not None else "데이터없음"
        print(f"[알림] 상장 {MIN_LISTING_YEARS}년 미만 제외: {col} (시작 {when})")
    return frame[keep]


def scale_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """컬럼별 MinMax 정규화로 [-1, 1] 범위에 매핑 (NaN-safe).

    의도: 절대값의 크기가 아니라 '방향성(상승/하락 흐름)' 중심으로 종목 간 관계를 보기 위함.
          종목별로 가격대(예: 삼성전자 vs 테슬라)가 크게 달라도 같은 척도 위에서 비교하려는 목적.

    참고 (중요):
        Pearson 상관계수 자체는 scale-invariant 이므로 이 정규화 단계를 제거해도
        ``analyze_overall`` / ``analyze_monthly`` 의 수치 결과는 동일하다.
        그러나 '방향성 중심 분석' 이라는 의도를 코드 형태로 남겨두는 게 이 함수의 목적이며,
        이후 다른 분석/시각화에서도 같은 normalized frame 을 재사용할 여지를 둔다.
        → 결과가 같다고 해서 무심코 제거하면 의도가 사라진다. 그대로 둘 것.
    """
    mins = np.nanmin(frame.values, axis=0)
    maxs = np.nanmax(frame.values, axis=0)
    spans = maxs - mins
    spans = np.where(spans == 0, 1.0, spans)
    scaled = 2.0 * (frame.values - mins) / spans - 1.0
    return pd.DataFrame(scaled, index=frame.index, columns=frame.columns)


def save_corr_csv(corr: pd.DataFrame, out_dir: Path, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{name}.csv"
    # utf-8-sig: Excel 에서 한글 컬럼명이 깨지지 않도록 BOM 포함. 일반 utf-8 로 바꾸지 말 것.
    corr.to_csv(csv_path, encoding="utf-8-sig")
    print(f"  저장: {csv_path.relative_to(BASE_DIR)} (종목수={len(corr)})")


def analyze_overall(scaled: pd.DataFrame, out_dir: Path) -> None:
    print(f"\n[{out_dir.name}] 통합 상관계수 (max n={len(scaled)})")
    corr = scaled.corr(method="pearson", min_periods=2)
    save_corr_csv(corr, out_dir, "overall")


def analyze_monthly(scaled: pd.DataFrame, out_dir: Path) -> None:
    """월(1~12)별로 잘라서 상관계수 계산 — 월별 계절성/이벤트 효과를 따로 보기 위함."""
    print(f"\n[{out_dir.name}] 월별 상관계수")
    months = scaled.index.month
    for m in range(1, 13):
        subset = scaled[months == m]
        if len(subset) < 2:
            print(f"  {m:02d}월: 데이터 부족 (n={len(subset)}) → 건너뜀")
            continue
        # min_periods=2 함정: 두 컬럼이 동시에 값이 있는 날이 2일만 있어도 상관계수가 계산된다.
        # → 결과 셀 중 일부는 표본수가 극히 적을 수 있으니 다운스트림에서 신뢰도 필터링 권장.
        corr = subset.corr(method="pearson", min_periods=2)
        save_corr_csv(corr, out_dir, f"{m:02d}")


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    reconfigure_stdio_utf8()

    today = pd.Timestamp.today().normalize()
    recent_cutoff = today - pd.DateOffset(years=RECENT_YEARS)
    listing_cutoff = today - pd.DateOffset(years=MIN_LISTING_YEARS)
    print(f"최근 컷오프: {recent_cutoff.date()} 이후 데이터를 {CORR_RECENT_DIR.name}/에 저장")
    print(f"상장 컷오프: {listing_cutoff.date()} 이후 시작한 종목은 제외 (상장 {MIN_LISTING_YEARS}년 미만)")

    index_names, kr_names, us_names = load_symbol_names()
    print(f"지수 {len(index_names)}개, 한국 {len(kr_names)}개, 미국 {len(us_names)}개")

    frame = build_close_frame(index_names, kr_names, us_names)
    # 순서 → 필터 순서 주의: 먼저 거르면 제외 종목이 reorder 에서 '데이터에 없음' 으로 잘못 찍힌다.
    frame = reorder_columns(frame, COLUMN_ORDER)
    frame = filter_by_listing(frame, listing_cutoff)
    print(f"통합 데이터 shape: {frame.shape}")
    print(f"기간: {frame.index.min().date()} ~ {frame.index.max().date()}")
    print(f"종목 수: {len(frame.columns)}")

    scaled = scale_frame(frame)

    # 전체 기간
    analyze_overall(scaled, CORR_ALL_DIR)
    analyze_monthly(scaled, CORR_ALL_DIR)

    # 최근 3년
    scaled_recent = scaled[scaled.index >= recent_cutoff]
    print(f"\n최근 {RECENT_YEARS}년 shape: {scaled_recent.shape} (cutoff={recent_cutoff.date()})")
    analyze_overall(scaled_recent, CORR_RECENT_DIR)
    analyze_monthly(scaled_recent, CORR_RECENT_DIR)


if __name__ == "__main__":
    main()
