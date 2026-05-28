# stock_data

[![Weekly update](https://github.com/syuka-fan/stock/actions/workflows/weekly-update.yml/badge.svg)](https://github.com/syuka-fan/stock/actions/workflows/weekly-update.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/syuka-fan/stock)](https://github.com/syuka-fan/stock/commits/main)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

한국/미국/지수 일봉 데이터를 수집하고, 상관관계·계절성 신호를 계산해 단일 HTML 대시보드(`index.html`)로 빌드하는 파이프라인.

GitHub Actions가 매주 토요일 12:00 KST에 자동으로 갱신·배포한다.

**라이브 데모**: https://syuka-fan.github.io/stock/

## 파이프라인

```
symbols.json
   │
   ▼
download.py ──► us_daily/  kr_daily/  index/  luxury_daily/   (+ download_state.json)
   │
   ├──► correlation_analysis.py ──► corr_all/      corr_recent/
   ├──► direction_analysis.py   ──► direction_all/ direction_recent/
   └──► luxury_trend.py         ──► luxury_trend.json
                                           │
                                           ▼
                                    build_index.py ──► index.html (PWA) + manifest.webmanifest
```

## 구성요소

| 파일 | 역할 |
| --- | --- |
| [symbols.json](symbols.json) | 추적 대상 티커 정의 (us / kr / index / luxury) |
| [common.py](common.py) | 경로 상수, 라벨↔파일명 규칙, 종가 프레임 로더 |
| [download.py](download.py) | yfinance / FinanceDataReader로 일봉 수집, `download_state.json`으로 증분 갱신 |
| [correlation_analysis.py](correlation_analysis.py) | MinMax 정규화 후 Pearson 상관계수 (전체 / 최근 / 월별) |
| [direction_analysis.py](direction_analysis.py) | 연내 누적 방향성(cumflow) 밴드/라인 |
| [luxury_trend.py](luxury_trend.py) | 명품주 원본 일봉을 티커별 [0,100] 정규화 → `luxury_trend.json` (분석 파이프라인과 독립) |
| [build_index.py](build_index.py) | `template.html`에 데이터(상관·방향·명품) 임베드, PWA 메타·SW·SWR 주입, `index.html` + `manifest.webmanifest` 출력 |
| [template.html](template.html) | 대시보드 UI 템플릿 (상관관계 / 트렌드 추세 / 명품 소비재 탭, 치환 앵커 포함) |
| [.github/workflows/weekly-update.yml](.github/workflows/weekly-update.yml) | 주간 자동 다운로드 → 분석 → PR 머지 → Pages 배포 |

## 로컬 실행

**요구사항**: Python 3.12+

```powershell
pip install -r requirements.txt

python download.py                # 일봉 수집/증분 갱신
python correlation_analysis.py    # 상관계수 CSV 생성
python direction_analysis.py      # 방향성 CSV 생성
python luxury_trend.py            # 명품주 정규화 JSON 생성
python build_index.py             # index.html + manifest.webmanifest 빌드
```

`download.py`는 `download_state.json`에 티커별 `last_date`를 기록하며, 다음 실행 시 `(last_date - 7일)`부터 받아 머지한다. 첫 실행이거나 상태 파일이 없으면 1990-01-01부터 수집한다.

## 데이터 소스

- **미국 주식·ETF**: `yfinance` → `us_daily/<TICKER>.csv`
- **한국 주식·ETF**: `FinanceDataReader` → `kr_daily/<TICKER>.csv`
- **지수**: `symbols.json`에서 지정한 소스로 → `index/<TICKER>.csv`
- **명품 소비재**: `yfinance` → `luxury_daily/<TICKER>.csv` (RMS.PA·MC.PA·KER.PA·1913.HK·456250.KS·LUXU.L)

티커 추가/제거는 [symbols.json](symbols.json)만 수정하면 전 파이프라인이 따라온다.

## 자동 배포

[weekly-update.yml](.github/workflows/weekly-update.yml)은:

1. `download.py` 실행 후 워킹트리 변화가 있거나 분석 출력 디렉터리(`luxury_daily` 포함) 중 하나라도 비어있으면 게이트 통과
2. `correlation_analysis.py` / `direction_analysis.py` / `luxury_trend.py` 병렬 실행
3. 결과를 `auto/update-<KST타임스탬프>` 브랜치로 push → PR 생성 → squash 머지
4. 머지된 main 기준으로 `build_index.py` 실행 후 `index.html`·`manifest.webmanifest`를 GitHub Pages로 배포

수동 실행은 Actions 탭의 **Run workflow**로 가능하다.

## 출력물

- `corr_all/`, `corr_recent/` — `overall.csv` + `01.csv` ~ `12.csv` (월별 상관계수)
- `direction_all/` — `(month, day_idx)`별 중앙값 밴드 (계절성)
- `direction_recent/` — 최근 5년. 종목별 `band`(통계 신뢰 가능) 또는 `lines`(상장 짧음, 연도별 raw)로 분기
- `luxury_trend.json` — 명품주 티커별 [0,100] 정규화 종가 (이름→날짜→값 2중 dict). 대시보드 "명품 소비재" 탭에서 사용
- `index.html` — 단일 파일 PWA. IndexedDB에 최신 페이로드를 캐시하고 SWR로 백그라운드 갱신
- `manifest.webmanifest` — PWA 매니페스트 (PNG 막대그래프 아이콘 임베드). `index.html`과 함께 배포

## 라이선스

[MIT License](LICENSE) © 2026 Minsu Chae
