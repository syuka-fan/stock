"""경로 상수, 라벨↔파일명 변환 규칙, 종가 프레임 로더.

모든 파이프라인(build_index / correlation_analysis / direction_analysis 등)이
동일한 라벨 형식과 파일명 규칙을 쓰도록 강제하기 위해 한 파일로 모음.
규칙을 바꿀 때는 LABEL_PREFIXES / safe_filename / stem_to_label 세 곳을 함께 검토할 것.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

SYMBOLS_PATH    = BASE_DIR / "symbols.json"
INDEX_DIR       = BASE_DIR / "index"
KR_DIR          = BASE_DIR / "kr_daily"
US_DIR          = BASE_DIR / "us_daily"
CORR_ALL_DIR    = BASE_DIR / "corr_all"
CORR_RECENT_DIR = BASE_DIR / "corr_recent"
DIR_ALL         = BASE_DIR / "direction_all"
DIR_RECENT      = BASE_DIR / "direction_recent"

# safe_filename / stem_to_label 의 파싱 규칙과 결합되어 있음.
# 형식("[XX] ") 을 바꾸면 두 함수의 split 로직도 함께 수정 필요.
LABEL_PREFIXES = {
    "index": "[IDX] ",
    "kr":    "[KR] ",
    "us":    "[US] ",
}


def reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        fn = getattr(stream, "reconfigure", None)
        if fn is not None:
            fn(encoding="utf-8")


def safe_filename(label: str) -> str:
    """라벨을 Windows 파일명으로 정규화.

    예: '[KR] 삼성전자' → 'KR_삼성전자'.

    주의: 손실 변환임 — 금지문자/공백을 모두 '_' 로 치환하므로
    원본 라벨에 '_' 가 포함되면 stem_to_label 라운드트립이 깨짐.
    """
    s = label.replace("[", "").replace("]", "").strip()
    for c in ' /\\?*"<>|:':
        s = s.replace(c, "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s


def stem_to_label(stem: str) -> str:
    """파일 stem 을 라벨로 환원.

    예: 'KR_삼성전자' → '[KR] 삼성전자'.

    safe_filename 의 완전 역함수가 아님 — safe_filename 이 손실 변환이라
    원본에 '_' 나 금지문자가 있던 경우 복원 보장 없음.
    단순한 케이스(prefix + 단일 토큰) 에서만 안전.
    """
    if "_" not in stem:
        return stem
    prefix, rest = stem.split("_", 1)
    return f"[{prefix}] {rest.replace('_', ' ')}"


def load_symbols_raw() -> dict:
    return json.loads(SYMBOLS_PATH.read_text(encoding="utf-8"))


def load_symbol_names() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """symbols.json 을 (index, kr, us) 이름 dict 세 개로 평탄화.

    index 항목은 원본에서 [이름, 티커] 튜플이라 첫 요소만 추출.
    raw 데이터(티커 포함)가 필요하면 load_symbols_raw 를 직접 호출할 것.
    """
    data = load_symbols_raw()
    return (
        {k: v[0] for k, v in data["index"].items()},
        dict(data["kr"]),
        dict(data["us"]),
    )


def load_close_series(csv_path: Path, label: str) -> pd.Series:
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    return df.set_index("Date")["Close"].rename(label).sort_index()


def build_close_frame(
    index_names: dict[str, str],
    kr_names: dict[str, str],
    us_names: dict[str, str],
) -> pd.DataFrame:
    """세 디렉터리의 종가 CSV 를 모아 outer-join 한 와이드 프레임.

    누락 CSV는 경고를 찍고 스킵 — 전체 파이프라인이 중단되지 않도록 한 정책.
    결과 DataFrame 에 해당 종목 컬럼이 빠질 수 있으므로 호출자는 NaN 처리 가정 금지.
    """
    series_list: list[pd.Series] = []
    for ticker, name in index_names.items():
        path = INDEX_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"[경고] 누락: {path}")
            continue
        series_list.append(load_close_series(path, f"{LABEL_PREFIXES['index']}{name}"))
    for ticker, name in kr_names.items():
        path = KR_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"[경고] 누락: {path}")
            continue
        series_list.append(load_close_series(path, f"{LABEL_PREFIXES['kr']}{name}"))
    for ticker, name in us_names.items():
        path = US_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"[경고] 누락: {path}")
            continue
        series_list.append(load_close_series(path, f"{LABEL_PREFIXES['us']}{name}"))
    return pd.concat(series_list, axis=1, join="outer", sort=True)
