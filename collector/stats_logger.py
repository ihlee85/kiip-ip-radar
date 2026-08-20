# -*- coding: utf-8 -*-
"""
stats_logger.py — 수집 시점(AI 선별 이전) 후보 기사 통계 기록 모듈
동향 통계 대시보드(dashboard.html)의 데이터 소스인 data/stats.json 을 생성·누적한다.

사용법 (collect_news.py 에 2줄 추가):
    from stats_logger import log_candidate_stats
    log_candidate_stats(candidates)   # ← AI 선별(classify) 호출 '직전'에 배치

기록 내용(본문 미저장 원칙 유지 — 집계 숫자만 저장):
    [{ "date": "2026-08-14", "total": 137, "runs": 2,
       "byCountry": {"US": 41, ...},
       "byKeyword": {"표준필수특허": 12, ...},        ← 추적 사전 매칭 언급 기사 수
       "coKw": {"표준필수특허|특허 심사 지연": 3, ...} ← 같은 기사 내 동시언급
    }, ...]
"""
import json
import os
import datetime
from collections import Counter

STATS_PATH = "data/stats.json"
KEEP_DAYS = 365          # 이보다 오래된 기록은 자동 정리
COUNTRY_KEY = "chint"    # collect_news.py 후보 dict의 국가 필드(소스 국가 힌트)
TEXT_KEYS = ("title", "desc")  # collect_news.py 후보 dict의 텍스트 필드

# ──────────────────────────────────────────────────────────────
# 추적 키워드 사전 — {한국어 표시명: [다국어 동의어(영·중·일 등)]}
# 후보 기사는 대부분 외국어 원문이므로 동의어가 매칭의 핵심.
# 사전에 키워드를 추가하면 '그날부터' 해당 키워드의 수집 기준 통계가 쌓인다.
# ──────────────────────────────────────────────────────────────
KEYWORD_TRACK = {
    "표준필수특허": ["standard essential patent", "standard-essential patent",
                    "SEP license", "FRAND", "标准必要专利", "標準必須特許", "표준필수특허"],
    "AI 생성물 저작권": ["AI copyright", "generative AI copyright", "AI-generated work",
                        "AI training data copyright", "人工智能生成", "AI生成物", "生成AI 著作権"],
    "AI 발명자": ["AI inventor", "AI inventorship", "DABUS", "人工智能发明", "AI発明"],
    "특허적격성": ["patent eligibility", "patentable subject matter", "Section 101",
                  "专利适格", "特許適格性"],
    "특허 심사 지연": ["patent backlog", "examination delay", "pendency",
                      "审查积压", "審査遅延", "심사 지연"],
    "영업비밀": ["trade secret", "商业秘密", "営業秘密", "영업비밀"],
    "상표 위조": ["counterfeit", "counterfeiting", "fake goods", "假冒", "模倣品", "위조상품"],
    "저작권 침해": ["copyright infringement", "piracy", "版权侵权", "著作権侵害"],
    "특허 분쟁": ["patent litigation", "patent lawsuit", "patent dispute",
                 "专利诉讼", "特許訴訟", "특허 소송"],
    "UPC": ["Unified Patent Court", "UPC ruling", "统一专利法院", "統一特許裁判所"],
    "디자인 보호": ["design protection", "design right", "industrial design",
                   "外观设计", "意匠"],
    "지리적 표시": ["geographical indication", "GI protection", "地理标志", "地理的表示"],
    "특허 수수료": ["patent fee", "official fee", "专利费", "特許料"],
    "악의적 상표출원": ["bad faith trademark", "trademark squatting", "恶意商标", "冒認商標"],
    "데이터 권리": ["data rights", "database right", "data ownership", "数据权利", "データ権利"],
}
# 소문자 사전 사전계산(성능)
_TRACK_LC = {kw: [s.lower() for s in syns] + [kw.lower()] for kw, syns in KEYWORD_TRACK.items()}


def _text_of(cand: dict) -> str:
    parts = []
    for k in TEXT_KEYS:
        v = cand.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts).lower()


def keyword_mentions(candidates):
    """후보 기사들에서 (키워드별 언급 기사 수, 동시언급 쌍 카운트)를 계산"""
    cnt, pair = Counter(), Counter()
    for c in candidates:
        text = _text_of(c)
        if not text:
            continue
        hits = [kw for kw, syns in _TRACK_LC.items() if any(s in text for s in syns)]
        for kw in hits:
            cnt[kw] += 1
        for i in range(len(hits)):
            for j in range(i + 1, len(hits)):
                a, b = sorted((hits[i], hits[j]))
                pair[a + "|" + b] += 1
    return dict(cnt), dict(pair)


def _merge_counter(dst: dict, src: dict):
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


def log_candidate_stats(candidates):
    """AI 선별 직전 호출 — 당일 entry에 합산 기록(하루 2회 실행 대응)"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    data = []
    if os.path.exists(STATS_PATH):
        try:
            with open(STATS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []

    by_country = Counter(str(c.get(COUNTRY_KEY, "기타")) for c in candidates)
    by_kw, co_kw = keyword_mentions(candidates)

    entry = next((e for e in data if e.get("date") == today), None)
    if entry:
        entry["total"] = entry.get("total", 0) + len(candidates)
        entry["runs"] = entry.get("runs", 1) + 1
        _merge_counter(entry.setdefault("byCountry", {}), by_country)
        _merge_counter(entry.setdefault("byKeyword", {}), by_kw)
        _merge_counter(entry.setdefault("coKw", {}), co_kw)
    else:
        data.append({
            "date": today,
            "total": len(candidates),
            "runs": 1,
            "byCountry": dict(by_country),
            "byKeyword": by_kw,
            "coKw": co_kw,
        })

    cutoff = (datetime.datetime.now() - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data = [e for e in data if e.get("date", "") >= cutoff]
    data.sort(key=lambda e: e.get("date", ""))

    os.makedirs(os.path.dirname(STATS_PATH) or ".", exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[stats_logger] 후보 {len(candidates)}건 기록 → {STATS_PATH} "
          f"(키워드 매칭 {sum(by_kw.values())}건)")


if __name__ == "__main__":
    # 간단 자가 테스트
    demo = [
        {"title": "USPTO issues new guidance on standard essential patent licensing and FRAND", "chint": "US"},
        {"title": "生成AIと著作権に関する報告書を公表", "chint": "JP"},
        {"title": "国家知识产权局公布打击恶意商标注册与假冒行动", "chint": "CN"},
        {"title": "EPO decision on patent eligibility of AI inventions (DABUS follow-up)", "chint": "EU"},
    ]
    cnt, pair = keyword_mentions(demo)
    print("언급:", cnt)
    print("동시언급:", pair)
