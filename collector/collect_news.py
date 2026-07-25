# -*- coding: utf-8 -*-
"""
KIIP Global IP Radar — 일일 뉴스 수집 에이전트
================================================
매일 1회 실행(GitHub Actions cron):
  1) IP5 관청·WIPO·전문매체의 RSS/Atom 피드를 수집
  2) 기존 아카이브(data/news.json)와 비교하여 신규 항목만 선별
  3) Claude API로 IP 관련성 판단 → 중복제거 → 주제분류 → 한글 요약
  4) data/news.json 에 누적 저장 (원문 재게시 없이 요약+링크만)

필요 환경변수:
  ANTHROPIC_API_KEY : Claude API 키 (GitHub Secrets에 저장)
의존성: pip install requests feedparser anthropic
"""
import json, os, re, hashlib, datetime, pathlib
import requests, feedparser
from anthropic import Anthropic

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "news.json"
TODAY = datetime.date.today().isoformat()

# ── 수집 대상 피드 (필요시 자유롭게 추가) ─────────────────────────
FEEDS = [
    # 관청·국제기구
    {"url": "https://www.uspto.gov/rss/uspto-news.xml",              "office": "USPTO", "country": "US"},
    {"url": "https://www.wipo.int/pressroom/en/rss.xml",             "office": "WIPO",  "country": "INT"},
    {"url": "https://www.epo.org/en/rss/news",                       "office": "EPO",   "country": "EU"},
    # 전문매체 (신뢰도 높은 콘텐츠 중심)
    {"url": "https://ipwatchdog.com/feed/",                          "office": "기타",  "country": "US"},
    {"url": "https://www.iam-media.com/rss",                         "office": "기타",  "country": "INT"},
    {"url": "https://patentlyo.com/feed",                            "office": "기타",  "country": "US"},
    # JPO/CNIPA/KIPO는 RSS 미제공 시 공지 페이지 크롤러를 별도 추가
]

TOPICS = ["AI·IP정책", "특허적격성", "심사시스템", "데이터·학습", "분쟁·소송", "통계·보고서"]

CLASSIFY_PROMPT = """당신은 한국지식재산연구원의 글로벌 IP 동향 수집 에이전트입니다.
아래 뉴스 후보 목록을 검토하여, 지식재산(특허·상표·디자인·저작권 제도, 특히 AI와 IP의 교차 영역) 정책·법제·심사·분쟁·통계와 관련된 항목만 선별하세요.

각 선별 항목에 대해 JSON 배열로만 응답하세요(설명 금지). 각 원소:
{{"idx": 후보번호, "topic": "{topics}" 중 하나, "title_ko": "한국어 제목(간결)", "summary_ko": "2문장 이내 한국어 요약(사실 위주, 원문 표현 복제 금지)"}}

관련 없는 항목, 단순 홍보, 이미 아카이브에 있는 것과 사실상 동일한 사건의 중복 보도는 제외하세요.

[최근 아카이브 제목 (중복 판단용)]
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

def fetch_candidates(archive):
    seen_ids = {i.get("id") for i in archive["items"]}
    out = []
    for feed in FEEDS:
        try:
            parsed = feedparser.parse(requests.get(feed["url"], timeout=20,
                        headers={"User-Agent": "KIIP-IP-Radar/0.1"}).content)
            for e in parsed.entries[:15]:
                title = re.sub(r"\s+", " ", e.get("title", "")).strip()
                url = e.get("link", "")
                if not title or not url:
                    continue
                iid = item_id(url, title)
                if iid in seen_ids:
                    continue
                pub = e.get("published_parsed") or e.get("updated_parsed")
                date = datetime.date(*pub[:3]).isoformat() if pub else TODAY
                # 최근 7일 이내만
                if (datetime.date.today() - datetime.date.fromisoformat(date)).days > 7:
                    continue
                out.append({"id": iid, "date": date, "title": title,
                            "desc": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400],
                            "url": url, "office": feed["office"],
                            "country": feed["country"],
                            "source": parsed.feed.get("title", feed["office"])})
        except Exception as ex:
            print(f"[warn] feed 실패 {feed['url']}: {ex}")
    return out

def classify(candidates, archive):
    if not candidates:
        return []
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    recent = "\n".join(f"- {i['title']}" for i in archive["items"][:60])
    cand_txt = "\n".join(f"[{n}] {c['title']} :: {c['desc'][:200]}"
                         for n, c in enumerate(candidates))
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(
            topics='", "'.join(TOPICS), recent_titles=recent or "(없음)",
            candidates=cand_txt)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"```(json)?", "", text).strip()
    try:
        picks = json.loads(text)
    except json.JSONDecodeError:
        print("[warn] 분류 응답 파싱 실패"); return []
    results = []
    for p in picks:
        try:
            c = candidates[p["idx"]]
        except (KeyError, IndexError):
            continue
        results.append({"id": c["id"], "date": c["date"], "country": c["country"],
                        "office": c["office"], "source": c["source"],
                        "topic": p.get("topic", "통계·보고서"),
                        "title": p.get("title_ko", c["title"]),
                        "summary": p.get("summary_ko", ""), "url": c["url"]})
    return results

def main():
    archive = load_archive()
    candidates = fetch_candidates(archive)
    print(f"신규 후보 {len(candidates)}건")
    new_items = classify(candidates, archive)
    print(f"선별 {len(new_items)}건")
    archive["items"] = sorted(new_items + archive["items"],
                              key=lambda x: x["date"], reverse=True)
    archive["updated"] = TODAY
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(archive, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"저장 완료: 누적 {len(archive['items'])}건")

if __name__ == "__main__":
    main()
