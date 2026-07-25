# -*- coding: utf-8 -*-
"""
KIIP Global IP Radar — 일일 뉴스 수집 에이전트 (v3)
====================================================
운영 구상: 원출처 자동수집·AI선별 → 담당자 검수 → KIIP 게재 → (향후) DB 자동화

수집 지침:
  - 수집원: KIIP IP 동향 News가 인용해 온 '원출처'를 직접 구독 (아래 SOURCES)
  - KIIP 지식재산동향 RSS는 수집원이 아니라 [기게재 사건 제외용 대조 목록]으로 사용
    → 아직 KIIP에 실리지 않은 새 소식만 선별하는 초안(draft) 도구 역할
  - 범위: 지식재산 전반 / 주제 6종 + AI 태그 + 국가 판별 / 하루 최대 10건
  - 저장: 요약 + 원문 링크만 (본문 재게시 없음)

필요 환경변수: ANTHROPIC_API_KEY (GitHub Secrets)
의존성: pip install requests feedparser anthropic
"""
import json, os, re, hashlib, datetime, pathlib
import requests, feedparser
from anthropic import Anthropic

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "news.json"
TODAY = datetime.date.today().isoformat()
DAILY_CAP = 10
RECENT_DAYS = 7
UA = {"User-Agent": "KIIP-IP-Radar/0.3 (pilot; contact via repo)"}

# ── 원출처 목록 (KIIP IP 동향 News 인용 출처 역추적 기반) ─────────────
# type: "rss" = RSS/Atom 피드, "html" = 뉴스목록 페이지 크롤링(간이)
# 첫 실행 로그의 소스별 상태표를 보고 죽은 피드는 교체/삭제하세요.
SOURCES = [
    # ── 미국 ──
    {"type":"rss","country":"US","source":"USPTO",
     "url":"https://www.uspto.gov/rss/uspto-news.xml"},
    {"type":"rss","country":"US","source":"미국 백악관",
     "url":"https://www.whitehouse.gov/presidential-actions/feed/"},
    {"type":"rss","country":"US","source":"Unified Patents",
     "url":"https://www.unifiedpatents.com/insights?format=rss"},
    {"type":"rss","country":"US","source":"IPWatchdog",
     "url":"https://ipwatchdog.com/feed/"},
    {"type":"rss","country":"US","source":"Patently-O",
     "url":"https://patentlyo.com/feed"},
    # ── 유럽 ──
    {"type":"rss","country":"EU","source":"EPO",
     "url":"https://www.epo.org/en/rss/news"},
    {"type":"rss","country":"EU","source":"영국 지식재산청(UKIPO)",
     "url":"https://www.gov.uk/government/organisations/intellectual-property-office.atom"},
    {"type":"rss","country":"EU","source":"JUVE Patent",
     "url":"https://www.juve-patent.com/feed/"},
    # ── 일본 (RSS 미제공 → 간이 크롤) ──
    {"type":"html","country":"JP","source":"일본 특허청(JPO)",
     "url":"https://www.jpo.go.jp/news/index.html",
     "link_pat":r'href="(/news/[^"]+\.html)"[^>]*>([^<]{10,120})<',
     "base":"https://www.jpo.go.jp"},
    # ── 중국 (RSS 미제공 → 간이 크롤; 해외 러너에서 차단될 수 있음) ──
    {"type":"html","country":"CN","source":"중국 국가지식산권국(CNIPA)",
     "url":"https://www.cnipa.gov.cn/col/col61/index.html",
     "link_pat":r'href="(/art/[^"]+\.html)"[^>]*>([^<]{10,120})<',
     "base":"https://www.cnipa.gov.cn"},
    # ── 국제기구 ──
    {"type":"rss","country":"INT","source":"WIPO",
     "url":"https://www.wipo.int/pressroom/en/rss.xml"},
    # ── 한국 ──
    # 지식재산처(moip.go.kr) 보도자료: RSS 유무 확인 필요 → 확인 후 추가 예정
]

# KIIP 기게재 대조용 공식 RSS (수집원 아님)
KIIP_TREND_RSS = "https://www.kiip.re.kr/rss/list.do?rsskey=trend"

TOPICS = ["AI·IP", "정책·법제", "심사·제도", "분쟁·소송", "보호·집행", "통계·보고서"]
COUNTRIES = ["US", "CN", "JP", "EU", "KR", "INT", "ETC"]

CLASSIFY_PROMPT = """당신은 한국지식재산연구원 'IP 동향 News' 담당자를 지원하는 수집 에이전트입니다.
아래 뉴스 후보에서 지식재산 전반(특허·상표·디자인·저작권·영업비밀의 정책·법제·심사·분쟁·보호·통계)과 관련된 항목만 선별하세요.
선별 결과는 담당자가 검수 후 게재 여부를 결정하는 '초안 후보'로 쓰입니다.

선별 규칙:
1. 관련 없는 항목, 단순 홍보·행사 안내는 제외
2. [KIIP 기게재 목록] 또는 [최근 아카이브]와 사실상 같은 사건은 제외 (이미 다룬 소식)
3. 같은 사건의 후보가 여럿이면 가장 원출처에 가까운 것 1건만 선택
4. 최대 {cap}건, 정책적 중요도가 높은 순

각 선별 항목을 JSON 배열로만 응답하세요(설명·마크다운 금지). 각 원소:
{{"idx": 후보번호,
  "topic": {topics} 중 하나 (핵심 주제 1개),
  "ai": AI·데이터 관련 여부 true/false,
  "country": {countries} 중 하나 (기사 내용 기준. 미국=US, 중국=CN, 일본=JP, 유럽 각국=EU, 한국=KR, WIPO 등 국제기구=INT, 그 외=ETC),
  "title_ko": "한국어 제목 (KIIP 동향뉴스 문체: '주체, 행위' 형식. 예: '미국 백악관, ○○ 행정명령 발표')",
  "summary_ko": "2문장 이내 한국어 요약 (사실 위주, 원문 문장 복제 금지)"}}

[KIIP 기게재 목록 (제외 대상)]
{kiip_published}

[최근 아카이브 (제외 대상)]
{recent_titles}

[뉴스 후보]
{candidates}
"""

def load_archive():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"updated": None, "items": []}

def item_id(url, title):
    return hashlib.md5((url + title).encode()).hexdigest()[:12]

def fetch_rss(src):
    resp = requests.get(src["url"], timeout=25, headers=UA)
    parsed = feedparser.parse(resp.content)
    out = []
    for e in parsed.entries[:20]:
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        url = e.get("link", "").strip()
        if not title or not url:
            continue
        pub = e.get("published_parsed") or e.get("updated_parsed")
        date = datetime.date(*pub[:3]).isoformat() if pub else TODAY
        out.append({"date": date, "title": title, "url": url,
                    "desc": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400]})
    return out

def fetch_html(src):
    resp = requests.get(src["url"], timeout=25, headers=UA)
    resp.encoding = resp.apparent_encoding
    out, seen = [], set()
    for m in re.finditer(src["link_pat"], resp.text):
        path, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        url = src["base"] + path
        if url in seen or len(title) < 8:
            continue
        seen.add(url)
        out.append({"date": TODAY, "title": title, "url": url, "desc": ""})
        if len(out) >= 12:
            break
    return out

def fetch_candidates(archive):
    seen_ids = {i.get("id") for i in archive["items"]}
    seen_urls = {i.get("url") for i in archive["items"]}
    out, status = [], []
    for src in SOURCES:
        try:
            rows = fetch_rss(src) if src["type"] == "rss" else fetch_html(src)
            fresh = 0
            for r in rows:
                age = (datetime.date.today()
                       - datetime.date.fromisoformat(r["date"])).days
                if age > RECENT_DAYS:
                    continue
                iid = item_id(r["url"], r["title"])
                if iid in seen_ids or r["url"] in seen_urls:
                    continue
                out.append({**r, "id": iid, "source": src["source"],
                            "chint": src["country"]})
                fresh += 1
            status.append(f"  ✔ {src['source']}: 신규 {fresh}건")
        except Exception as ex:
            status.append(f"  ✘ {src['source']}: 실패 ({type(ex).__name__}: {ex})")
    print("── 소스별 수집 상태 ──")
    print("\n".join(status))
    return out

def fetch_kiip_published():
    """KIIP 지식재산동향 RSS에서 최근 게재 제목을 가져와 중복 제외에 사용"""
    try:
        resp = requests.get(KIIP_TREND_RSS, timeout=25, headers=UA)
        parsed = feedparser.parse(resp.content)
        titles = [re.sub(r"\s+", " ", e.get("title", "")).strip()
                  for e in parsed.entries[:60]]
        print(f"KIIP 기게재 대조 목록 {len(titles)}건 확보")
        return titles
    except Exception as ex:
        print(f"[warn] KIIP RSS 대조 실패: {ex}")
        return []

def classify(candidates, archive, kiip_titles):
    if not candidates:
        return []
    client = Anthropic()
    recent = "\n".join(f"- {i['title']}" for i in archive["items"][:60])
    kiip = "\n".join(f"- {t}" for t in kiip_titles)
    cand_txt = "\n".join(f"[{n}] ({c['source']}) {c['title']} :: {c['desc'][:200]}"
                         for n, c in enumerate(candidates))
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(
            cap=DAILY_CAP,
            topics=json.dumps(TOPICS, ensure_ascii=False),
            countries=json.dumps(COUNTRIES, ensure_ascii=False),
            kiip_published=kiip or "(없음)",
            recent_titles=recent or "(없음)",
            candidates=cand_txt)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"```(json)?", "", text).strip()
    try:
        picks = json.loads(text)
    except json.JSONDecodeError:
        print("[warn] 분류 응답 파싱 실패:", text[:300]); return []
    results = []
    for p in picks[:DAILY_CAP]:
        try:
            c = candidates[int(p["idx"])]
        except (KeyError, ValueError, IndexError):
            continue
        topic = p.get("topic", "정책·법제")
        country = p.get("country", c.get("chint", "ETC"))
        results.append({"id": c["id"], "date": c["date"],
                        "country": country if country in COUNTRIES else "ETC",
                        "source": c["source"],
                        "topic": topic if topic in TOPICS else "정책·법제",
                        "ai": bool(p.get("ai", False)),
                        "title": p.get("title_ko", c["title"]),
                        "summary": p.get("summary_ko", ""),
                        "url": c["url"]})
    return results

def main():
    archive = load_archive()
    kiip_titles = fetch_kiip_published()
    candidates = fetch_candidates(archive)
    print(f"신규 후보 {len(candidates)}건")
    new_items = classify(candidates, archive, kiip_titles)
    print(f"선별 {len(new_items)}건 (상한 {DAILY_CAP}건)")
    archive["items"] = sorted(new_items + archive["items"],
                              key=lambda x: x["date"], reverse=True)
    archive["updated"] = TODAY
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(archive, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"저장 완료: 누적 {len(archive['items'])}건")

if __name__ == "__main__":
    main()
