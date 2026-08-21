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
    "표준필수특허": ["standard essential patent", "standard-essential patent", "SEP", "FRAND", "标准必要专利", "標準必須特許", "표준필수특허"],
    "특허침해": ["patent infringement", "infringed patent", "infringing patent", "专利侵权", "特許侵害", "특허침해", "특허 침해"],
    "특허소송": ["patent litigation", "patent lawsuit", "patent suit", "patent case", "patent dispute", "专利诉讼", "特許訴訟", "특허소송", "특허 소송"],
    "특허 무효": ["patent invalid", "invalidation", "revocation", "inter partes review", "PTAB", "无效宣告", "特許無効", "무효심판", "특허 무효"],
    "금지명령": ["injunction", "injunctive relief", "禁令", "差止", "가처분", "금지명령"],
    "영업비밀": ["trade secret", "economic espionage", "商业秘密", "営業秘密", "영업비밀"],
    "상표 위조": ["counterfeit", "counterfeiting", "fake goods", "knockoff", "假冒", "模倣品", "위조상품", "짝퉁"],
    "상표 분쟁": ["trademark infringement", "trademark dispute", "trademark lawsuit", "商标侵权", "商標侵害", "상표권 침해", "상표 분쟁"],
    "악의적 상표출원": ["bad faith trademark", "trademark squatting", "恶意注册", "恶意商标", "冒認出願", "악의적 출원"],
    "저작권 침해": ["copyright infringement", "piracy", "pirated", "版权侵权", "著作権侵害", "저작권 침해", "불법복제"],
    "AI 생성물 저작권": ["AI-generated", "AI generated", "generative AI", "AI copyright", "生成AI", "AI生成", "人工智能生成", "생성형 AI", "AI 생성물"],
    "AI 학습데이터": ["training data", "text and data mining", "TDM", "data mining exception", "学習データ", "训练数据", "학습데이터", "학습 데이터"],
    "AI 발명자": ["AI inventor", "inventorship", "DABUS", "AI発明", "AI 발명"],
    "특허적격성": ["patent eligibility", "patentable subject matter", "section 101", "subject matter eligibility", "专利适格", "特許適格", "특허적격"],
    "심사 지연": ["backlog", "pendency", "examination delay", "审查周期", "審査遅延", "심사 지연", "심사지연"],
    "심사기준": ["examination guideline", "examination guidance", "examination standard", "审查指南", "審査基準", "심사기준", "심사 기준"],
    "특허 수수료": ["patent fee", "fee schedule", "official fee", "fee increase", "专利费", "特許料", "수수료"],
    "UPC": ["unified patent court", "UPC", "统一专利法院", "統一特許裁判所"],
    "단일특허": ["unitary patent", "单一专利", "単一特許", "단일특허"],
    "반도체 특허": ["semiconductor patent", "chip patent", "semiconductor IP", "芯片专利", "半導体特許", "반도체 특허"],
    "의약품 특허": ["pharmaceutical patent", "drug patent", "patent linkage", "biosimilar", "patent term extension", "药品专利", "医薬品特許", "의약품 특허", "바이오시밀러"],
    "지리적 표시": ["geographical indication", "地理标志", "地理的表示", "지리적 표시"],
    "디자인 보호": ["design patent", "industrial design", "registered design", "design right", "外观设计", "意匠", "디자인권", "디자인 보호"],
    "특허 라이선스": ["patent license", "patent licensing", "royalty", "licensing deal", "专利许可", "ライセンス契約", "라이선스", "실시권"],
    "기술유출": ["technology theft", "technology leakage", "tech transfer restriction", "技术泄露", "技術流出", "기술유출", "국가핵심기술"],
    "직무발명": ["employee invention", "职务发明", "職務発明", "직무발명"],
    "IP 금융": ["IP finance", "IP financing", "patent-backed", "IP-backed", "知识产权质押", "知財金融", "IP 금융", "특허 담보"],
    "강제실시": ["compulsory license", "compulsory licensing", "强制许可", "強制実施", "강제실시"],
    "병행수입": ["parallel import", "平行进口", "並行輸入", "병행수입"],
    "오픈소스": ["open source license", "open-source license", "OSS", "开源", "オープンソース", "오픈소스"],
    "데이터 권리": ["data rights", "database right", "data ownership", "data act", "数据产权", "データ権利", "데이터 권리"],
    "국제출원": ["PCT", "Madrid system", "Hague system", "international application", "国际申请", "国際出願", "마드리드", "헤이그", "국제출원"],
    "특허 통계": ["patent filings", "patent statistics", "filing statistics", "申请量", "出願件数", "출원 통계", "특허 통계"],
}
# 소문자 사전 사전계산(성능)
import re as _re
def _compile(syn):
    s = syn.lower()
    if _re.fullmatch(r"[a-z0-9]{2,6}", s):          # 짧은 영문 약어 → 단어 경계 매칭(september≠SEP)
        return ("re", _re.compile(r"\b" + _re.escape(s) + r"\b"))
    return ("sub", s)
_TRACK_LC = {kw: [_compile(s) for s in set(syns + [kw])] for kw, syns in KEYWORD_TRACK.items()}
def _hit(text, matcher):
    kind, pat = matcher
    return bool(pat.search(text)) if kind == "re" else (pat in text)

KW_DICT_PATH = "data/kw_dict.json"
def dump_kw_dict():
    """대시보드가 게재 기사 검색에 쓰도록 사전을 함께 배포"""
    try:
        os.makedirs(os.path.dirname(KW_DICT_PATH) or ".", exist_ok=True)
        with open(KW_DICT_PATH, "w", encoding="utf-8") as f:
            json.dump(KEYWORD_TRACK, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[stats_logger] kw_dict 저장 실패:", e)


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
        hits = [kw for kw, ms in _TRACK_LC.items() if any(_hit(text, m) for m in ms)]
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
    dump_kw_dict()


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
