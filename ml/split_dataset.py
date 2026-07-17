# -*- coding: utf-8 -*-
# =============================================================================
# split_dataset.py
#   ml/split_dataset.ipynb 를 출력문 포함해 변환한 스크립트.
#
#   변환일        : 2026-07-18
#   출력 기록 기준 : 아래 주석의 [OUT] 블록은 원본 노트북 실행 결과이며,
#                    데이터 범위는 2026-07-16(코스피200 선물 7/16)까지 존재.
#
#   ※ [OUT] 로 시작하는 주석은 원본 노트북에 저장돼 있던 셀 출력을 그대로
#     옮겨 둔 것이라, 재실행 시점/데이터에 따라 값이 달라질 수 있음.
# =============================================================================

# -----------------------------------------------------------------------------
# [markdown] train / eval / test 분할 (Date, Close) — 전체기간·분기청크·6:2:2 셔플
#
# 대상: 코스피(^KS11), 삼성전자, SK하이닉스, 현대차, 하나금융지주, KB금융,
#       우리금융지주, 코스피200롱150숏선물
#
# 규칙
# - 전체 기간(합집합): 가장 이른 시작(코스피 1995-05-02) ~ 가장 늦은 끝(2026-07-16).
#   각 종목 상장 이전 구간은 Close=-1.
# - 달력: 모든 달력일(주말·공휴일 포함). 거래 없는 날(휴무일)은 Close=-1
#   → 전 종목이 동일한 날짜 그리드.
# - 청크: 달력 분기(1~3, 4~6, 7~9, 10~12월) 단위.
# - 배정: 분기 청크를 train:eval:test = 6:2:2(청크 개수 기준)로 셔플(무작위) 배정.
#   청크는 통째로 한 split 에.
# - 강제 배정(FORCED): 2026Q2(4~6월) → train, 2026Q3(7~9월) → test
#   (2026Q3 데이터는 7/16까지 존재).
# - 모든 종목의 train/eval/test 날짜 구간은 동일.
# - 개별 종목 파일은 최종적으로 날짜순 정렬. 추가로 종목을 열로 합친
#   train.csv / eval.csv / test.csv 생성.
#
# > 원본/기존 코드는 수정하지 않고 ml/ 아래로 복사만 한다. SEED 로 셔플 재현.
# -----------------------------------------------------------------------------

import os
import pandas as pd
import numpy as np

# ── 경로 설정 (ml/ 또는 프로젝트 루트 어디서 실행해도 동작) ──────────
CWD = os.getcwd()
ROOT = os.path.dirname(CWD) if os.path.basename(CWD) == 'ml' else CWD
if not os.path.exists(os.path.join(ROOT, 'symbols.json')):
    raise RuntimeError(f'프로젝트 루트를 찾지 못했습니다 (symbols.json 없음): {ROOT}')

KR_DIR    = os.path.join(ROOT, 'kr_daily')
INDEX_DIR = os.path.join(ROOT, 'index')
OUT_DIR   = os.path.join(ROOT, 'ml')
print('ROOT   :', ROOT)
print('out(ml):', OUT_DIR)

# [OUT]
# ROOT   : C:\Users\pc\Project\stock_data
# out(ml): C:\Users\pc\Project\stock_data\ml


# ── 대상 종목: (source, code, 표시이름) ────────────────────────────
# source: 'index' → index/<code>.csv,  'kr' → kr_daily/<code>.csv
TARGETS = [
    ('index', '^KS11',  '코스피'),
    ('kr',    '005930', '삼성전자'),
    ('kr',    '000660', 'SK하이닉스'),
    ('kr',    '005380', '현대차'),
    ('kr',    '086790', '하나금융지주'),
    ('kr',    '105560', 'KB금융'),
    ('kr',    '316140', '우리금융지주'),
    ('kr',    '360140', '코스피200롱150숏선물'),
]

def src_path(source, code):
    return os.path.join(INDEX_DIR if source == 'index' else KR_DIR, f'{code}.csv')

# 통합 파일(train.csv 등)의 열 순서
COL_ORDER = ['코스피', '코스피200롱150숏선물', '삼성전자', 'SK하이닉스',
             '현대차', 'KB금융', '우리금융지주', '하나금융지주']

# ── 분할 파라미터 ──────────────────────────────────────────────────
RATIO   = (6, 2, 2)   # train : eval : test (청크 개수 기준)
SEED    = 42           # 셔플 재현성
MISSING = -1           # 휴무일·상장전 Close 대체값

# 특정 분기를 특정 split 에 강제 배정 (분기 → split)
FORCED = {
    pd.Period('2026Q2', 'Q'): 'train',   # 2026-04~06 → 반드시 train
    pd.Period('2026Q3', 'Q'): 'test',    # 2026-07~09 → 반드시 test (데이터는 7/16까지)
}


# ── 전체 기간(union) 로드 & 마스터 달력 그리드 ─────────────────────
frames = {}
gmin = gmax = None
for source, code, name in TARGETS:
    d = pd.read_csv(src_path(source, code), parse_dates=['Date'])[['Date', 'Close']]
    d = d.dropna(subset=['Date']).sort_values('Date')
    frames[name] = d
    gmin = d['Date'].min() if gmin is None else min(gmin, d['Date'].min())
    gmax = d['Date'].max() if gmax is None else max(gmax, d['Date'].max())

grid = pd.date_range(gmin, gmax, freq='D')   # 모든 달력일(주말·공휴일 포함)
print('전체 기간(union):', gmin.date(), '~', gmax.date(), ' | 그리드 행수:', len(grid))

# ── 분기 청크 & 6:2:2 셔플 배정 (FORCED 강제) ─────────────────────
quarters = pd.PeriodIndex(sorted(pd.unique(grid.to_period('Q'))))
n = len(quarters)
n_test  = round(n * RATIO[2] / sum(RATIO))
n_eval  = round(n * RATIO[1] / sum(RATIO))
n_train = n - n_eval - n_test
need = {'train': n_train, 'eval': n_eval, 'test': n_test}

# 강제 분기 제외한 나머지를 셔플
rng = np.random.default_rng(SEED)
pool = [q for q in quarters if q not in FORCED]
pool = [pool[i] for i in rng.permutation(len(pool))]

SPLIT_ORDER = {'train': [], 'eval': [], 'test': []}
for q, s in FORCED.items():                       # 강제 분기 먼저 배치
    SPLIT_ORDER[s].append(q)
_it = iter(pool)                                  # 나머지를 남은 정원만큼 채움
for s in ('train', 'eval', 'test'):
    while len(SPLIT_ORDER[s]) < need[s]:
        SPLIT_ORDER[s].append(next(_it))
assign = {q: s for s, qs in SPLIT_ORDER.items() for q in qs}

print(f'청크(분기) 수: {n}  →  train:{len(SPLIT_ORDER["train"])}  '
      f'eval:{len(SPLIT_ORDER["eval"])}  test:{len(SPLIT_ORDER["test"])}  (target {RATIO})')
for q, s in FORCED.items():
    assert assign[q] == s, f'{q} 이 {s} 에 배정되지 않음'
    print(f'FORCED {q} → {assign[q]}')

# [OUT]
# 전체 기간(union): 1995-05-02 ~ 2026-07-16  | 그리드 행수: 11399
# 청크(분기) 수: 126  →  train:76  eval:25  test:25  (target (6, 2, 2))
# FORCED 2026Q2 → train
# FORCED 2026Q3 → test


# ── 종목별로 그리드 정렬 → 휴무일/상장전 -1 → split 별 개별 파일 저장 ─
# (여기서는 셔플된 청크 순서로 저장, 다음 단계에서 날짜순 재정렬)
for s in ('train', 'eval', 'test'):
    os.makedirs(os.path.join(OUT_DIR, s), exist_ok=True)

summary = []
for source, code, name in TARGETS:
    d = frames[name].groupby('Date', as_index=False)['Close'].last()   # 중복일 제거
    d = d.set_index('Date').reindex(grid)                              # 전체 달력 그리드
    d['Close'] = d['Close'].fillna(MISSING)                            # 휴무일·상장전 = -1
    d.index.name = 'Date'
    qkey = d.index.to_period('Q')

    row = {'name': name, 'code': code}
    for split, qlist in SPLIT_ORDER.items():
        out = pd.concat([d.loc[qkey == q, ['Close']] for q in qlist])  # 셔플 청크 순서
        out = out.reset_index()
        if (out['Close'] % 1 == 0).all():                             # 정수 종가는 정수 유지(지수는 소수)
            out['Close'] = out['Close'].astype('int64')
        out['Date'] = out['Date'].dt.strftime('%Y-%m-%d')
        out[['Date', 'Close']].to_csv(
            os.path.join(OUT_DIR, split, f'{name}.csv'), index=False, encoding='utf-8-sig')
        row[split] = len(out)
    summary.append(row)

print('각 split 개별 파일 행수 (모든 종목 동일):')
print(pd.DataFrame(summary)[['name', 'code', 'train', 'eval', 'test']])

# [OUT]
# 각 split 개별 파일 행수 (모든 종목 동일):
#             name    code  train  eval  test
# 0            코스피   ^KS11   6909  2280  2210
# 1           삼성전자  005930   6909  2280  2210
# 2         SK하이닉스  000660   6909  2280  2210
# 3            현대차  005380   6909  2280  2210
# 4         하나금융지주  086790   6909  2280  2210
# 5           KB금융  105560   6909  2280  2210
# 6         우리금융지주  316140   6909  2280  2210
# 7  코스피200롱150숏선물  360140   6909  2280  2210


# ── 개별 종목 split 파일을 날짜순으로 다시 정렬해서 덮어쓰기 ────────
for split in ('train', 'eval', 'test'):
    for source, code, name in TARGETS:
        p = os.path.join(OUT_DIR, split, f'{name}.csv')
        df = pd.read_csv(p).sort_values('Date').reset_index(drop=True)
        df.to_csv(p, index=False, encoding='utf-8-sig')
print('개별 종목 파일 날짜순 재정렬 완료')

# [OUT]
# 개별 종목 파일 날짜순 재정렬 완료


# ── 통합 파일: ml/train.csv, eval.csv, test.csv (Date + 종목별 열) ──
for split in ('train', 'eval', 'test'):
    merged = None
    for name in COL_ORDER:
        s = pd.read_csv(os.path.join(OUT_DIR, split, f'{name}.csv'))[['Date', 'Close']]
        s = s.rename(columns={'Close': name})
        merged = s if merged is None else merged.merge(s, on='Date', how='outer')
    merged = merged.sort_values('Date').reset_index(drop=True)   # 날짜순 정렬
    merged = merged[['Date'] + COL_ORDER]                        # 열 순서 고정
    merged.to_csv(os.path.join(OUT_DIR, f'{split}.csv'), index=False, encoding='utf-8-sig')
    print(f'{split}.csv : {merged.shape[0]}행 x {merged.shape[1]}열')

# [OUT]
# train.csv : 6909행 x 9열
# eval.csv : 2280행 x 9열
# test.csv : 2210행 x 9열


# ── 검증 ───────────────────────────────────────────────────────────
import glob

# 1) split 간 분기 겹침 없음
sets = {s: set(str(q) for q in qs) for s, qs in SPLIT_ORDER.items()}
assert not (sets['train'] & sets['eval']) and not (sets['train'] & sets['test']) and not (sets['eval'] & sets['test'])

# 2) 개별 종목 파일: 날짜순 & split 별 동일 날짜 시퀀스
for s in ['train', 'eval', 'test']:
    files = sorted(glob.glob(os.path.join(OUT_DIR, s, '*.csv')))
    ref = pd.read_csv(files[0])['Date']
    assert ref.is_monotonic_increasing, f'{s}: 날짜순 정렬 안 됨'
    for f in files[1:]:
        assert pd.read_csv(f)['Date'].tolist() == ref.tolist(), f'{s}: {f} 날짜 시퀀스 불일치'
    print(f'{s}/: {len(files)}개 종목, 각 {len(ref)}행, 날짜순·시퀀스 동일 OK')

# 3) 통합 파일: 열 구성/순서 + 날짜순
EXPECT = ['Date'] + COL_ORDER
for s in ['train', 'eval', 'test']:
    w = pd.read_csv(os.path.join(OUT_DIR, f'{s}.csv'))
    assert list(w.columns) == EXPECT, f'{s}.csv 열 구성 불일치: {list(w.columns)}'
    assert w['Date'].is_monotonic_increasing, f'{s}.csv 날짜순 아님'
    print(f'{s}.csv: {w.shape[0]}행 x {w.shape[1]}열, 날짜순 OK')

# 4) 강제 배정 확인: 2026Q2 → train, 2026Q3 → test
w_tr = pd.read_csv(os.path.join(OUT_DIR, 'train.csv'))
w_te = pd.read_csv(os.path.join(OUT_DIR, 'test.csv'))
assert ((w_tr['Date'] >= '2026-04-01') & (w_tr['Date'] <= '2026-06-30')).any(), '2026Q2 가 train 에 없음'
assert not ((w_tr['Date'] >= '2026-07-01') & (w_tr['Date'] <= '2026-09-30')).any(), '2026Q3 가 train 에 섞임'
assert ((w_te['Date'] >= '2026-07-01') & (w_te['Date'] <= '2026-09-30')).any(), '2026Q3 가 test 에 없음'
print('\n강제 배정 OK: 2026Q2→train, 2026Q3→test')

print('\n[test.csv] 2026Q3(7/1~) 미리보기:')
print(w_te[w_te['Date'] >= '2026-07-01'].head(8).to_string(index=False))
print('\nOK: 전체 검증 통과')

# [OUT]
# train/: 8개 종목, 각 6909행, 날짜순·시퀀스 동일 OK
# eval/: 8개 종목, 각 2280행, 날짜순·시퀀스 동일 OK
# test/: 8개 종목, 각 2210행, 날짜순·시퀀스 동일 OK
# train.csv: 6909행 x 9열, 날짜순 OK
# eval.csv: 2280행 x 9열, 날짜순 OK
# test.csv: 2210행 x 9열, 날짜순 OK
#
# 강제 배정 OK: 2026Q2→train, 2026Q3→test
#
# [test.csv] 2026Q3(7/1~) 미리보기:
#       Date     코스피  코스피200롱150숏선물   삼성전자  SK하이닉스    현대차   KB금융  우리금융지주  하나금융지주
# 2026-07-01 8303.41          24085 314500 2560000 487500 158500   29350  116400
# 2026-07-02 7648.09          23820 286000 2187000 482000 165000   30200  120800
# 2026-07-03 8088.34          25600 309500 2425000 492000 170100   30750  125600
# 2026-07-04   -1.00             -1     -1      -1     -1     -1      -1      -1
# 2026-07-05   -1.00             -1     -1      -1     -1     -1      -1      -1
# 2026-07-06 8051.33          26320 318000 2343000 502000 170900   31150  124800
# 2026-07-07 7656.31          24795 296000 2201000 479500 173200   31550  126900
# 2026-07-08 7246.79          25545 277500 2076000 462500 171000   30700  122300
#
# OK: 전체 검증 통과
