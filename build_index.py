"""template.html 에 데이터와 PWA 자산을 주입해 index.html 을 생성.

사용:
    python build_index.py
"""
from __future__ import annotations

import base64
import io
import json
import csv
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from common import (
    BASE_DIR,
    DIR_ALL,
    DIR_RECENT,
    CORR_ALL_DIR,
    CORR_RECENT_DIR,
    reconfigure_stdio_utf8,
    safe_filename,
    stem_to_label,
)

SUMMARY_PATH = DIR_ALL / "_summary.csv"
TEMPLATE_PATH = BASE_DIR / "template.html"
OUTPUT_PATH = BASE_DIR / "index.html"
MANIFEST_PATH = BASE_DIR / "manifest.webmanifest"
LUXURY_TREND_PATH = BASE_DIR / "luxury_trend.json"


def pick_corr_months(today_month: int) -> tuple[int, int, int]:
    prev_m = today_month - 1 if today_month > 1 else 12
    next_m = today_month + 1 if today_month < 12 else 1
    return prev_m, today_month, next_m

def pick_trend_months(today_month: int) -> tuple[int, int, int, int, int]:
    def wrap(m: int) -> int:
        return ((m - 1) % 12) + 1
    return (wrap(today_month - 2), wrap(today_month - 1), today_month,
            wrap(today_month + 1), wrap(today_month + 2))

def load_ticker_order() -> list[str]:
    """_summary.csv의 ticker 순서대로 stem 리스트 반환. 없으면 빈 리스트."""
    if not SUMMARY_PATH.exists():
        return []
    stems: list[str] = []
    with SUMMARY_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ticker = r.get("ticker", "").strip()
            if ticker:
                stems.append(safe_filename(ticker))
    return stems

def list_ticker_stems() -> list[str]:
    """direction_all/*.csv (excl. _summary). _summary.csv 순서 우선, 누락분은 알파벳 뒤붙임."""
    available = {p.stem for p in DIR_ALL.glob("*.csv") if p.stem != "_summary"}
    ordered = load_ticker_order()
    if ordered:
        result: list[str] = []
        seen: set[str] = set()
        for s in ordered:
            if s in available and s not in seen:
                result.append(s)
                seen.add(s)
        for s in sorted(available - seen):
            result.append(s)
        return result
    return sorted(available)


def read_corr_csv(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return None
    header = rows[0]
    labels = header[1:]
    matrix: list[list[float | None]] = []
    for r in rows[1:]:
        row_values: list[float | None] = []
        for v in r[1:]:
            v = v.strip()
            if v == "" or v.lower() == "nan":
                row_values.append(None)
            else:
                try:
                    row_values.append(float(v))
                except ValueError:
                    row_values.append(None)
        matrix.append(row_values)
    return {"labels": labels, "matrix": matrix}

def build_corr_section(dir_path: Path, key: str, label: str,
                       prev_m: int, curr_m: int, next_m: int) -> dict:
    targets = [
        ("overall", "overall"),
        (f"{prev_m:02d}", f"{prev_m:02d}"),
        (f"{curr_m:02d}", f"{curr_m:02d}"),
        (f"{next_m:02d}", f"{next_m:02d}"),
    ]
    charts: dict = {}
    for stem, ckey in targets:
        data = read_corr_csv(dir_path / f"{stem}.csv")
        if data is None:
            print(f"[경고] 누락: {dir_path / f'{stem}.csv'}")
            continue
        charts[ckey] = data
    return {"key": key, "label": label, "charts": charts}

def read_all_csv(path: Path, target_months: set[int]) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                month = int(r["month"])
                if month not in target_months:
                    continue
                rows.append({
                    "month": month,
                    "day_idx": int(r["day_idx"]),
                    "n_years": int(r["n_years"]),
                    "median": float(r["median"]),
                    "upper": float(r["upper"]),
                    "lower": float(r["lower"]),
                })
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda r: (r["month"], r["day_idx"]))
    return rows


def read_recent_csv(path: Path, target_months: set[int]) -> tuple[str, list[dict]]:
    """direction_recent CSV 읽기. 헤더 sniffing으로 (mode, rows) 반환.

    mode: 'band' | 'lines' | 'missing' | 'empty'
    """
    if not path.exists():
        return ("missing", [])
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        if "median" in fields:
            rows: list[dict] = []
            for r in reader:
                try:
                    month = int(r["month"])
                    if month not in target_months:
                        continue
                    rows.append({
                        "month": month,
                        "day_idx": int(r["day_idx"]),
                        "n_years": int(r["n_years"]),
                        "median": float(r["median"]),
                        "upper": float(r["upper"]),
                        "lower": float(r["lower"]),
                    })
                except (KeyError, ValueError):
                    continue
            rows.sort(key=lambda r: (r["month"], r["day_idx"]))
            return ("band", rows)
        if "cumflow" in fields:
            rows = []
            for r in reader:
                try:
                    month = int(r["month"])
                    if month not in target_months:
                        continue
                    rows.append({
                        "year": int(r["year"]),
                        "month": month,
                        "day_idx": int(r["day_idx"]),
                        "cumflow": float(r["cumflow"]),
                    })
                except (KeyError, ValueError):
                    continue
            rows.sort(key=lambda r: (r["year"], r["month"], r["day_idx"]))
            return ("lines", rows)
        return ("empty", [])



def load_luxury_trend() -> dict:
    """luxury_trend.py 산출물을 그대로 읽어 payload 에 통과시킨다.

    luxury 계산 로직은 luxury_trend.py 에만 존재하며, 여기선 완성된 JSON 을
    임베드만 한다 (분석 파이프라인 비통합 원칙 유지). 파일이 없으면 빈 dict.
    """
    if not LUXURY_TREND_PATH.exists():
        print("[경고] luxury_trend.json 없음 — luxury 데이터 없이 빌드")
        return {}
    return json.loads(LUXURY_TREND_PATH.read_text(encoding="utf-8"))


def build_payload() -> dict:
    today = date.today()

    prev_m, curr_m, next_m = pick_corr_months(today.month)
    corr = {
        "months": [prev_m, curr_m, next_m],
        "tab_keys": ["overall", f"{prev_m:02d}", f"{curr_m:02d}", f"{next_m:02d}"],
        "sections": [
            build_corr_section(CORR_ALL_DIR, "all", "전체 기간",
                               prev_m, curr_m, next_m),
            build_corr_section(CORR_RECENT_DIR, "recent", "최근 3년",
                               prev_m, curr_m, next_m),
        ],
    }

    trend_months = list(pick_trend_months(today.month))
    target_set = set(trend_months)

    stems = list_ticker_stems()
    default_order = stems[:]

    tickers: list[dict] = []
    for stem in stems:
        title = stem_to_label(stem)

        rows_all = read_all_csv(DIR_ALL / f"{stem}.csv", target_set)
        all_chart = None
        if rows_all:
            all_chart = {
                "type": "band",
                "shade_low_sample": True,
                "mode_label": None,
                "rows": rows_all,
            }

        mode, rows_recent = read_recent_csv(DIR_RECENT / f"{stem}.csv", target_set)
        recent_chart = None
        if mode == "band" and rows_recent:
            recent_chart = {
                "type": "band",
                "shade_low_sample": True,
                "mode_label": {"cls": "band", "text": "5년 통합"},
                "rows": rows_recent,
            }
        elif mode == "lines" and rows_recent:
            recent_chart = {
                "type": "lines",
                "shade_low_sample": False,
                "mode_label": {"cls": "lines", "text": "연도별"},
                "rows": rows_recent,
            }

        if all_chart is None and recent_chart is None:
            print(f"[경고] direction 데이터 전무: {stem}")
            continue

        tickers.append({
            "stem": stem,
            "title": title,
            "all": all_chart,
            "recent": recent_chart,
        })

    return {
        "built_at": today.isoformat(),
        "built_month": today.month,
        "corr": corr,
        "trend": {
            "months": trend_months,
            "min_samples": 3,
            "default_order": default_order,
            "tickers": tickers,
        },
        "luxury": load_luxury_trend(),
    }



# template.html 의 메인 <script> 첫 줄과 정확히 일치해야 함.
# replace_once 가 1회 매칭을 강제하므로, template 쪽이 변경되면 빌드가 즉시 실패한다.
PAYLOAD_LINE = "const PAYLOAD = JSON.parse(`{{PAYLOAD_JSON}}`);"

ALL_MARKERS = (
    "{{BUILT_AT_META}}",
    "{{PWA_HEAD}}",
    "{{PWA_SW_SOURCE}}",
    "{{PWA_BOOTSTRAP}}",
    "{{PAYLOAD_JSON}}",
)

# SW revalidate 는 HTML 첫 N 바이트만 읽어 built_at 메타를 찾는다.
# 메타가 이 범위를 벗어나면 revalidate 가 항상 stale 을 반환 → 사용자가 갱신본을 못 받음.
# BOOTSTRAP_BLOCK 의 text.slice 와 main() 의 검증부가 이 상수를 공유한다.
HEAD_REVALIDATE_BUDGET_BYTES = 2048


# 아이콘 색. theme_color 와 동일한 파랑 배경에 흰 막대그래프.
ICON_BG = "#2563eb"
ICON_FG = "#ffffff"
# 막대 높이 비율(좌→우). 데이터가 오르내리는 추세 느낌.
ICON_BAR_HEIGHTS = (0.45, 0.72, 0.55, 0.95)


def _render_icon(size: int, *, square: bool, content_frac: float) -> bytes:
    """막대그래프 아이콘을 PNG 바이트로 렌더.

    emoji <text> 는 OS 가 아이콘을 래스터화할 때 emoji 폰트에 의존해 깨지므로
    벡터 도형(rect)으로 직접 그린다. 4x 슈퍼샘플 후 LANCZOS 다운스케일로 안티에일리어싱.

    square=True  : 배경을 캔버스 전체로 채움 (maskable / apple-touch — OS 가 모양을 클리핑).
    square=False : iOS 풍 라운드 코너 (마스킹되지 않고 그대로 보이는 'any' 용).
    content_frac : 막대가 차지하는 중앙 영역 비율. maskable 은 안전영역(중앙 원) 안에 들도록 작게.
    """
    ss = 4
    px = size * ss
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if square:
        draw.rectangle([0, 0, px - 1, px - 1], fill=ICON_BG)
    else:
        draw.rounded_rectangle([0, 0, px - 1, px - 1], radius=int(px * 0.22), fill=ICON_BG)

    margin = px * (1 - content_frac) / 2
    area = px - 2 * margin
    base_y = margin + area
    n = len(ICON_BAR_HEIGHTS)
    gap = area * 0.06
    bar_w = (area - gap * (n - 1)) / n
    for i, h in enumerate(ICON_BAR_HEIGHTS):
        x0 = margin + i * (bar_w + gap)
        y0 = base_y - area * h
        draw.rectangle([x0, y0, x0 + bar_w, base_y], fill=ICON_FG)

    img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _png_data_uri(png_bytes: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"


def build_pwa_assets() -> tuple[str, str]:
    """(apple-touch-icon data URI, manifest JSON 문자열) 생성.

    PNG 래스터 아이콘(192/512 any, 512 maskable)을 manifest 에 임베드한다.
    SVG/emoji 만 제공하면 Android Chrome 이 설치 시 앱 이름 첫 글자로
    아이콘을 자동 생성(글자 폴백)하므로 PNG 가 필수.
    """
    manifest = {
        "name": "데이터 기반 종목 분석",
        "short_name": "종목 분석",
        # start_url/scope 는 manifest 파일 위치 기준 상대경로로 해석된다.
        # (과거 data: URI manifest 에선 기준 URL 이 없어 해석이 깨졌음)
        "start_url": "./index.html",
        "scope": "./",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#2563eb",
        "icons": [
            {"src": _png_data_uri(_render_icon(192, square=False, content_frac=0.62)),
             "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": _png_data_uri(_render_icon(512, square=False, content_frac=0.62)),
             "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": _png_data_uri(_render_icon(512, square=True, content_frac=0.55)),
             "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    apple_data_uri = _png_data_uri(_render_icon(180, square=True, content_frac=0.6))
    return apple_data_uri, manifest_json


def pwa_head_block(apple_data_uri: str) -> str:
    return (
        '<meta name="theme-color" content="#2563eb" media="(prefers-color-scheme: light)">\n'
        '<meta name="theme-color" content="#0b1220" media="(prefers-color-scheme: dark)">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n'
        '<meta name="apple-mobile-web-app-title" content="종목 분석">\n'
        f'<link rel="apple-touch-icon" href="{apple_data_uri}">\n'
        '<link rel="manifest" href="./manifest.webmanifest">'
    )


SW_SOURCE_BLOCK = """<script id="pwa-sw-source" type="text/plain">
const CACHE = 'index-html-v1';
self.addEventListener('install',  e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(e.request);
    const network = fetch(e.request).then(r => { if (r.ok) cache.put(e.request, r.clone()); return r; }).catch(() => cached);
    return cached || network;
  })());
});
</script>"""


# revalidate 정규식은 <meta name="app-built-at" content="..."> 를 찾는다.
# (JSON payload 의 built_at 을 첫 4KB 에서 찾던 기존 방식은 페이로드 위치상 실패함)
BOOTSTRAP_BLOCK = r"""<script>
(function () {
  const DB_NAME = 'index-html-pwa', STORE = 'payload', KEY = 'latest';
  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  async function idbGet() {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }
  async function idbPut(payload) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put({ built_at: payload.built_at, payload }, KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }
  window.__resolvePayload = async function (embedded) {
    try {
      const stored = await idbGet();
      if (stored && stored.built_at > embedded.built_at) return stored.payload;
    } catch (e) {}
    return embedded;
  };
  window.__revalidate = async function (currentBuiltAt) {
    try {
      const resp = await fetch(location.href, { cache: 'no-cache' });
      if (!resp.ok) return;
      const text = await resp.text();
      const head = text.slice(0, __HEAD_BUDGET__);
      const m = head.match(/name="app-built-at"\s+content="([^"]+)"/);
      if (!m || m[1] <= currentBuiltAt) return;
      const doc = new DOMParser().parseFromString(text, 'text/html');
      const el  = doc.getElementById('payload-data');
      if (!el) return;
      await idbPut(JSON.parse(el.textContent));
    } catch (e) {}
  };
  if ('serviceWorker' in navigator) {
    try {
      const src = document.getElementById('pwa-sw-source').textContent;
      const url = URL.createObjectURL(new Blob([src], { type: 'application/javascript' }));
      navigator.serviceWorker.register(url).catch(() => {});
    } catch (e) {}
  }
})();
</script>""".replace("__HEAD_BUDGET__", str(HEAD_REVALIDATE_BUDGET_BYTES))


def replace_once(content: str, old: str, new: str, label: str) -> str:
    """앵커가 정확히 1회만 매칭될 때 치환. 0회/2회+ 면 즉시 실패."""
    count = content.count(old)
    if count == 0:
        raise RuntimeError(f"[치환 실패] 앵커 미발견: {label}")
    if count > 1:
        raise RuntimeError(f"[치환 실패] 앵커 중복 ({count}회): {label}")
    return content.replace(old, new, 1)


def _inject_head_markers(content: str, payload: dict, apple_data_uri: str) -> str:
    built_at_meta = f'<meta name="app-built-at" content="{payload["built_at"]}">'

    content = replace_once(content,
        "<!-- {{BUILT_AT_META}} -->", built_at_meta, "BUILT_AT_META")
    content = replace_once(content,
        "<!-- {{PWA_HEAD}} -->",
        pwa_head_block(apple_data_uri), "PWA_HEAD")
    content = replace_once(content,
        "<!-- {{PWA_SW_SOURCE}} -->", SW_SOURCE_BLOCK, "PWA_SW_SOURCE")
    content = replace_once(content,
        "<!-- {{PWA_BOOTSTRAP}} -->", BOOTSTRAP_BLOCK, "PWA_BOOTSTRAP")
    return content


def _inject_payload_script(content: str, payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    # payload 가 <script type="application/json"> 안으로 들어간다.
    # HTML 파서는 본문 안 어디서든 </ 를 만나면 태그 종료를 시도하므로,
    # 페이로드에 </foo> 가 있으면 JSON 본문이 잘려나간다. JSON 문법은 깨지지 않는 형태로 회피.
    payload_safe = payload_json.replace("</", "<\\/")
    payload_data_tag = (
        f'<script id="payload-data" type="application/json">{payload_safe}</script>'
    )

    # 메인 <script> 시작부: payload-data 태그를 앞에 끼우고, 본 <script> 는 async IIFE 로 감싼다.
    # PAYLOAD 라인을 __resolvePayload 호출로 치환해 SWR (IndexedDB 최신본 우선) 진입점을 만든다.
    script_open = "<script>\n" + PAYLOAD_LINE
    script_open_new = (
        payload_data_tag + "\n"
        "<script>\n"
        "(async () => {\n"
        'const PAYLOAD_EMBEDDED = JSON.parse(document.getElementById("payload-data").textContent);\n'
        "const PAYLOAD = await window.__resolvePayload(PAYLOAD_EMBEDDED);"
    )
    content = replace_once(content, script_open, script_open_new, "SCRIPT_OPEN+PAYLOAD")

    # 메인 <script> 종료부: IIFE 를 닫으면서 revalidate 를 idle 시점에 1회 트리거.
    script_close = "renderAll();\n</script>"
    script_close_new = (
        "renderAll();\n"
        "(window.requestIdleCallback || setTimeout)(() => window.__revalidate(PAYLOAD.built_at), 1500);\n"
        "})();\n"
        "</script>"
    )
    return replace_once(content, script_close, script_close_new, "SCRIPT_CLOSE")


def _verify_output(content: str) -> None:
    for marker in ALL_MARKERS:
        if marker in content:
            raise RuntimeError(f"[검증 실패] 미처리 마커 잔존: {marker}")
    head_bytes = content.encode("utf-8")[:HEAD_REVALIDATE_BUDGET_BYTES]
    if b'name="app-built-at"' not in head_bytes:
        raise RuntimeError(
            f"[검증 실패] BUILT_AT 메타가 첫 {HEAD_REVALIDATE_BUDGET_BYTES}B 안에 없음 "
            "(SWR revalidate 깨짐)"
        )


def main() -> int:
    reconfigure_stdio_utf8()

    if not TEMPLATE_PATH.exists():
        print(f"[오류] {TEMPLATE_PATH} 없음")
        return 1

    payload = build_payload()
    if (not payload["trend"]["tickers"]
            and not any(s["charts"] for s in payload["corr"]["sections"])):
        print("[오류] 임베드할 데이터가 없습니다.")
        return 1

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    # 모든 앵커는 LF 기준으로 작성돼 있다.
    # Windows 에서 편집된 template 의 CRLF 가 섞이면 replace_once 가 0회 매칭으로 실패한다.
    content = raw.replace("\r\n", "\n").replace("\r", "\n")

    apple_data_uri, manifest_json = build_pwa_assets()
    content = _inject_head_markers(content, payload, apple_data_uri)
    content = _inject_payload_script(content, payload)
    _verify_output(content)

    # newline="\n" 은 LF 출력 보장 (write_text 가 플랫폼 기본 줄바꿈으로 재변환하는 것을 차단).
    OUTPUT_PATH.write_text(content, encoding="utf-8", newline="\n")
    # manifest 는 별도 파일로 출력해야 내부 상대경로(start_url/scope)가 정상 해석된다.
    # index.html 과 같은 디렉터리에 두고, 배포 시 함께 업로드돼야 한다 (weekly-update.yml).
    MANIFEST_PATH.write_text(manifest_json, encoding="utf-8", newline="\n")

    print(f"생성 완료: {OUTPUT_PATH}")
    print(f"  · 크기: {len(raw):,} -> {len(content):,} bytes")
    print(f"  · payload built_at: {payload['built_at']}")
    print(f"  · 트렌드 티커: {len(payload['trend']['tickers'])}개")
    print(f"  · 상관관계 서브탭: {', '.join(payload['corr']['tab_keys'])}")
    print(f"  · manifest: {MANIFEST_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
