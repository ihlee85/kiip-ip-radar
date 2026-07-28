# -*- coding: utf-8 -*-
"""
KIIP Global IP Radar — 일일 뉴스 수집 에이전트 (v4)
====================================================
v4 변경: 연구원 공식 '해외 IP 동향 수집 자료원 목록' 87개 출처 전면 반영
  - 수집 방식 3종: RSS / HTML 목록 크롤(범용 파서) / 구글뉴스 검색(gnews)
  - 직접 접속 실패·0건 시 구글뉴스 site: 검색으로 자동 폴백
  - 유료 매체(MLEX·IAM·Thomson Reuters·Bloomberg)는 제목 참고용으로 gnews 수집
  - 접속불가처(인도네시아·사우디)는 비활성 등록, 복구 시 enabled=True로 전환
  - 후보 폭증 대비: 소스당 상한 + 전체 후보 상한(우선순위 순 절사)

운영 구상: 원출처 자동수집·AI선별 → 담당자 검수 → KIIP 게재 → (향후) DB 자동화
필요 환경변수: ANTHROPIC_API_KEY (GitHub Secrets) / DRY=1 이면 AI선별 없이 수집만 테스트
의존성: pip install requests feedparser anthropic googlenewsdecoder
"""
import json, os, re, hashlib, datetime, pathlib, urllib.parse
import requests, feedparser

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "news.json"
TODAY = datetime.date.today().isoformat()
DAILY_CAP = 10          # AI 선별 기본 상한
PURGE_BEFORE = "2026-07-25"  # 서비스 개시일 이전 날짜의 잔재 항목 제거
RECENT_DAYS = 3         # 최신 72시간 이내 기사만 수집
PER_SOURCE = 5          # 소스당 후보 상한
MAX_CANDIDATES = 150    # AI에 전달할 전체 후보 상한(우선순위 순)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "ko,en;q=0.8",
      "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, */*"}

# ══════════════════════════════════════════════════════════════════
# 수집원 목록 — 연구원 '해외 IP 동향 수집 자료원 목록(260427ver)' 기반
#   typ: "rss" | "html"(목록 크롤+gnews 폴백) | "gnews"(구글뉴스 검색)
#   pat: html일 때 기사링크 경로 필터(정규식, 없으면 범용 추출)
#   q  : gnews 검색어(없으면 site:도메인)
# ══════════════════════════════════════════════════════════════════
def S(country, source, url, typ="html", pat=None, q=None, enabled=True, note=""):
    return {"country": country, "source": source, "url": url, "typ": typ,
            "pat": pat, "q": q, "enabled": enabled, "note": note}

SOURCES = [
  # ─── 한국 (4) ───
  S("KR","지식재산처(언론보도)","",typ="gnews",q='"지식재산처"',note="구글뉴스 한국판"),
  S("KR","대한민국 정책브리핑","",typ="gnews",q='site:korea.kr 지식재산 OR 특허 OR 상표 OR 저작권'),
  S("KR","저작권 정책(문체부·위원회)","",typ="gnews",q='"한국저작권위원회" OR (문체부 저작권)'),
  S("KR","특허법원·심판 동향","",typ="gnews",q='특허법원 OR 특허심판원 OR 특허소송'),
  # ─── 미국 (13) ───
  S("US","미국 특허상표청(USPTO)","https://www.uspto.gov/about-us/news-updates",pat=r'/about-us/news-updates/'),
  S("US","미국 무역대표부(USTR)","https://ustr.gov/about/policy-offices/press-office/news",pat=r'/about/policy-offices/press-office/'),
  S("US","미국 백악관","https://www.whitehouse.gov/presidential-actions/feed/",typ="rss"),
  S("US","미국 저작권청","https://copyright.gov/newsnet/archive/",pat=r'/newsnet/'),
  S("US","미국 국제무역위원회(USITC)","https://www.usitc.gov/news_releases",pat=r'/press_room/news_release/'),
  S("US","미국 연방거래위원회(FTC)","https://www.ftc.gov/news-events/news/press-releases",pat=r'/news-events/news/press-releases/'),
  S("US","미국 상공회의소","https://www.uschamber.com/about/newsroom?topic=13566"),
  S("US","IPWatchdog","https://ipwatchdog.com/feed/",typ="rss"),
  S("US","Patently-O","https://patentlyo.com/feed",typ="rss"),
  S("US","Patent Docs","https://www.patentdocs.org/atom.xml",typ="rss"),
  S("US","Law360 IP","https://www.law360.com/ip/rss",typ="rss"),
  S("US","Unified Patents","https://www.unifiedpatents.com/insights?format=rss",typ="rss"),
  # 유료 매체: 제목 참고용 → 구글뉴스 검색 수집
  S("US","MLEX(유료)","",typ="gnews",q='site:mlex.com "intellectual property" OR patent OR trademark',note="유료, 제목 참고용"),
  S("US","IAM(유료)","",typ="gnews",q='site:iam-media.com',note="유료, 제목 참고용"),
  S("US","Thomson Reuters","",typ="gnews",q='site:reuters.com "intellectual property" OR patent OR trademark OR copyright',note="키워드 검색"),
  S("US","Bloomberg","",typ="gnews",q='site:bloomberg.com "intellectual property" OR patent OR trademark OR copyright',note="유료, 키워드 검색"),
  # ─── 중국 (26) ───
  S("CN","中 지식산권국 공고","https://www.cnipa.gov.cn/col/col74/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 통지","https://www.cnipa.gov.cn/col/col75/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 정책해석","https://www.cnipa.gov.cn/col/col66/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 주요뉴스","https://www.cnipa.gov.cn/col/col53/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 백서","https://www.cnipa.gov.cn/col/col91/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 연도보고","https://www.cnipa.gov.cn/col/col94/index.html",pat=r'/art/'),
  S("CN","中 지식산권국 미디어시점","https://www.cnipa.gov.cn/col/col55/index.html",pat=r'/art/'),
  S("CN","中 상무부 뉴스레터","https://www.mofcom.gov.cn/xwfb/index.html",pat=r'/xwfb/'),
  S("CN","中 상무부 주요뉴스","https://www.mofcom.gov.cn/szyw/index.html",pat=r'/szyw/'),
  S("CN","中 상무부 정무공개","https://www.mofcom.gov.cn/zwgk/index.html",pat=r'/zwgk/'),
  S("CN","中 시장감독관리총국","https://www.samr.gov.cn/xw/zj/index.html",pat=r'/xw/'),
  S("CN","中 시장감독관리총국 정책해석","https://www.samr.gov.cn/zw/zjwj/zcjd/index.html",pat=r'/zw/'),
  S("CN","中 국가판권국 통지공고","https://www.ncac.gov.cn/xxfb/tzgg/",pat=r'/xxfb/'),
  S("CN","中 국가판권국 주요뉴스","https://www.ncac.gov.cn/xxfb/ywxx/",pat=r'/xxfb/'),
  S("CN","中 국가판권국 업계동향","https://www.ncac.gov.cn/xxfb/yjdt/",pat=r'/xxfb/'),
  S("CN","中 최고인민법원 뉴스","https://www.court.gov.cn/zixun/gengduo/24.html",pat=r'/(zixun|fabu)-xiangqing'),
  S("CN","中 최고인민법원 사법문건","https://www.court.gov.cn/fabu/gengduo/17.html",pat=r'/fabu-xiangqing'),
  S("CN","中 최고인민법원 통지","https://www.court.gov.cn/fabu/gengduo/18.html",pat=r'/fabu-xiangqing'),
  S("CN","中 최고인민법원 의견","https://www.court.gov.cn/fabu/gengduo/19.html",pat=r'/fabu-xiangqing'),
  S("CN","中 최고인민법원 사법해석","https://www.court.gov.cn/fabu/gengduo/16.html",pat=r'/fabu-xiangqing'),
  S("CN","中 최고인민검찰원 뉴스","https://www.spp.gov.cn/spp/gjybs/index.shtml",pat=r'/spp/'),
  S("CN","中 최고인민검찰원 중점추천","https://www.spp.gov.cn/spp/zdgz/index.shtml",pat=r'/spp/'),
  S("CN","IPR Daily","https://www.iprdaily.cn/",pat=r'iprdaily'),
  S("CN","지식산권망","http://www.cnipr.com/",pat=r'cnipr'),
  S("CN","지식산권보","https://www.iprchn.com/",pat=r'iprchn'),
  S("CN","인민망","",typ="gnews",q='site:people.com.cn 知识产权 OR 专利 OR 商标 OR 版权',note="키워드 검색"),
  # ─── 일본 (15) ───
  S("JP","일본 특허청(JPO)","https://www.jpo.go.jp/news/shinchaku/koshin/index.html",pat=r'/news/'),
  S("JP","日 지식재산전략본부","https://www.cas.go.jp/jp/seisakukaigi/titeki2/index.html",pat=r'/titeki2/'),
  S("JP","日 경제산업성(METI)","https://www.meti.go.jp/",pat=r'/press/'),
  S("JP","日 총무성","https://www.soumu.go.jp/menu_kyotsuu/whatsnew/index.html",pat=r'/menu_news/'),
  S("JP","日 문화청","https://www.bunka.go.jp/whats_new.html",pat=r'bunka\.go\.jp'),
  S("JP","日 후생노동성","https://www.mhlw.go.jp/stf/new-info/",pat=r'/stf/'),
  S("JP","日 INPIT 지원뉴스","https://www.inpit.go.jp/shien/topic/index.html",pat=r'/shien/'),
  S("JP","日 INPIT 정보뉴스","https://www.inpit.go.jp/katsuyo/topic/index.html",pat=r'/katsuyo/'),
  S("JP","日 INPIT 인재뉴스","https://www.inpit.go.jp/jinzai/topic/index.html",pat=r'/jinzai/'),
  S("JP","JETRO 지재보호","https://www.jetro.go.jp/biznewstop/ip/biznews/",pat=r'/biznews/'),
  S("JP","日 지적재산고등재판소","https://www.courts.go.jp/news/index.html",pat=r'/news/'),
  S("JP","일본경제신문","",typ="gnews",q='site:nikkei.com 知的財産 OR 特許 OR 商標 OR 著作権',note="키워드 검색"),
  S("JP","요미우리신문","",typ="gnews",q='site:yomiuri.co.jp 知的財産 OR 特許 OR 商標 OR 著作権',note="키워드 검색"),
  S("JP","patentsalon","https://www.patentsalon.com/",pat=r'patentsalon'),
  S("JP","patentresult","https://www.patentresult.co.jp/",pat=r'patentresult'),
  # ─── 유럽 (6) ───
  S("EU","유럽 특허기구(EPO)","https://www.epo.org/en/news-events/news",pat=r'/en/news-events/news/'),
  S("EU","EU 지식재산기구(EUIPO)","https://www.euipo.europa.eu/en/news-and-events/news",pat=r'/news'),
  S("EU","영국 지식재산청(UKIPO)","https://www.gov.uk/government/organisations/intellectual-property-office.atom",typ="rss"),
  S("EU","EU 집행위원회","https://commission.europa.eu/news-and-media/news_en",pat=r'/news'),
  S("EU","유럽 통합특허법원(UPC)","https://www.unifiedpatentcourt.org/en/news",pat=r'/en/news/'),
  S("EU","EU 지식재산네트워크(EUIPN)","https://www.euipn.org/en/news-and-events",pat=r'/news'),
  S("EU","JUVE Patent","https://www.juve-patent.com/feed/",typ="rss"),
  # ─── 국제기구 (8) ───
  S("INT","세계지식재산기구(WIPO)","https://www.wipo.int/pressroom/en/rss.xml",typ="rss"),
  S("INT","ID5","https://id-five.org/about/id5-news/",pat=r'id-five'),
  S("INT","TM5","https://tmfive.org/news-and-events/",pat=r'tmfive'),
  S("INT","OECD","https://www.oecd.org/en/about/newsroom.html",pat=r'/en/'),
  S("INT","국제상표협회(INTA)","https://www.inta.org/about/inta-news/",pat=r'/news|/perspectives'),
  S("INT","세계무역기구(WTO)","https://www.wto.org/english/news_e/news_e.htm",pat=r'/english/news_e/',note="목록 URL 오류 정정"),
  S("INT","APEC","https://www.apec.org/what-we-achieved/newsroom",pat=r'/press-releases|/news'),
  S("INT","유엔산업개발기구(UNIDO)","https://www.unido.org/news",pat=r'/news/'),
  # ─── 기타 (아세안·기타국) ───
  S("ETC","베트남 지식재산청 공지","https://www.ipvietnam.gov.vn/web/english/announcement",pat=r'ipvietnam'),
  S("ETC","베트남 지식재산청 연차보고","https://www.ipvietnam.gov.vn/en_US/web/english/annual-report",pat=r'ipvietnam'),
  S("ETC","베트남 지식재산청 국내동향","https://www.ipvietnam.gov.vn/en_US/web/english/domestic-ip-activities",pat=r'ipvietnam'),
  S("ETC","베트남 지식재산청 세계동향","https://www.ipvietnam.gov.vn/en_US/web/english/world-ip-activities",pat=r'ipvietnam'),
  S("ETC","말레이시아 지식재산청","https://www.myipo.gov.my/",pat=r'myipo'),
  S("ETC","말레이시아 지식재산청 미디어","https://www.myipo.gov.my/ms/media/",pat=r'myipo'),
  S("ETC","싱가포르 지식재산청 뉴스","https://www.ipos.gov.sg/news/news-collection/",pat=r'/news'),
  S("ETC","싱가포르 지식재산청 연구","https://www.ipos.gov.sg/global-ip-hub/research-and-studies/",pat=r'ipos\.gov\.sg'),
  S("ETC","싱가포르 지식재산청 간행물","https://www.ipos.gov.sg/global-ip-hub/research-and-studies/publications/",pat=r'ipos\.gov\.sg'),
  S("ETC","인도네시아 지식재산청","https://www.dgip.go.id/",enabled=False,note="26.4월 기준 한국 접속차단"),
  S("ETC","호주 지식재산청","https://www.ipaustralia.gov.au/news-and-community",pat=r'/news|/about-us'),
  S("ETC","사우디 지식재산청","https://www.saip.gov.sa/en/news",enabled=False,note="26.4월 기준 사이트 셧다운"),
  S("ETC","캐나다 지식재산청 공지","https://ised-isde.canada.ca/site/canadian-intellectual-property-office/en/notices-and-updates",pat=r'intellectual-property-office'),
  S("ETC","캐나다 지식재산청 간행물","https://ised-isde.canada.ca/site/canadian-intellectual-property-office/en/publications",pat=r'intellectual-property-office'),
  S("ETC","필리핀 지식재산청 뉴스","https://www.ipophil.gov.ph/updates/news/",pat=r'ipophil'),
  S("ETC","필리핀 지식재산청 공지","https://www.ipophil.gov.ph/updates/announcements/",pat=r'ipophil'),
  S("ETC","필리핀 지식재산청 보도자료","https://www.ipophil.gov.ph/updates/press-releases/",pat=r'ipophil'),
  S("ETC","아프리카 지재기구(ARIPO)","https://www.aripo.org/news/",pat=r'aripo'),
  S("ETC","아프리카 지재기구(OAPI)","https://oapi.int/en/media/news/",pat=r'oapi'),
  S("ETC","아시아 IP Law","https://asiaiplaw.com/",pat=r'asiaiplaw'),
]

# KIIP 기게재 대조용 (수집원 아님)
KIIP_TREND_RSS = "https://www.kiip.re.kr/rss/list.do?rsskey=trend"
KIIP_TREND_BOARD = "https://www.kiip.re.kr/board/trend/list.do?bd_gb=trend&bd_cd=1&bd_item=0"

TOPICS = ["AI·IP", "정책·법제", "심사·제도", "분쟁·소송", "보호·집행", "통계·보고서"]
COUNTRIES = ["US", "CN", "JP", "EU", "KR", "INT", "ETC"]
# 자국 행정 소식 중심 관청·기관 소스는 국가 고정 (오분류 방지)
FIXED_COUNTRY_PREFIX = {"中 ": "CN", "日 ": "JP", "일본 ": "JP"}

CLASSIFY_PROMPT = """당신은 한국지식재산연구원 'IP 동향 News' 담당자를 지원하는 수집 에이전트입니다.
아래 뉴스 후보에서 지식재산 전반(특허·상표·디자인·저작권·영업비밀의 정책·법제·심사·분쟁·보호·통계)과 관련된 항목만 선별하세요.
선별 결과는 담당자가 검수 후 게재 여부를 결정하는 '초안 후보'로 쓰입니다.

선별 규칙:
1. 관련 없는 항목, 단순 홍보·행사·채용·시스템점검 공지는 제외. 단, 관청 공식 발표는 행정공지가 아닌 한 관련성을 폭넓게 인정하세요. 일본어·중국어 후보는 제목만으로 판단하되 언어를 이유로 제외하지 마세요. '(유료)' 표시 출처는 제목 정보만으로 판단하세요. 발행일 미상 후보 중 제목상 명백히 과거 자료(연차보고서·백서·지난 연도 통계·과년도 행사 등)는 제외하세요. 최신 72시간 내 소식만 선별 대상입니다.
2. [KIIP 기게재 목록] 또는 [최근 아카이브]와 사실상 같은 사건은 제외 (이미 다룬 소식)
3. 같은 사건의 후보가 여럿이면 가장 원출처에 가까운 것 1건만 선택
4. 기본 상한 {cap}건, 정책적 중요도가 높은 순
5. 국가별 상한: 같은 국가(country 기준) 기사는 하루 최대 3건까지만 선별하여 특정국 편중을 방지하세요.
6. 국가 다양성 보장: 관련성 있는 후보가 존재하는 국가(US/CN/JP/EU/KR/INT/ETC)가 선별 결과에서 빠져 있으면, 그 국가에서 가장 중요한 1건을 추가로 선별하세요. 이 추가분은 상한 {cap}건을 초과해도 됩니다. 단, 지식재산 관련성이 없는 기사를 다양성 명목으로 억지로 포함하지는 마세요.

각 선별 항목을 JSON 배열로만 응답하세요(설명·마크다운 금지). 각 원소:
{{"idx": 후보번호,
  "topic": {topics} 중 하나 (핵심 주제 1개),
  "ai": AI·데이터 관련 여부 true/false,
  "country": {countries} 중 하나. 판별 기준은 '행위 주체 기관의 소속 국가'입니다. 예: CNIPA 발표는 국제협력 내용이라도 CN, JPO 발표는 JP, 영국 지식재산청은 EU. WIPO·WTO 등 국제기구가 행위 주체일 때만 INT.
  "title_ko": "한국어 제목 (KIIP 동향뉴스 문체: '주체, 행위' 형식. 예: '미국 백악관, ○○ 행정명령 발표')",
  "summary_ko": "2문장 이내 한국어 요약 (사실 위주, 원문 문장 복제 금지)"}}

[KIIP 기게재 목록 (제외 대상)]
{kiip_published}

[최근 아카이브 (제외 대상)]
{recent_titles}

[뉴스 후보]
{candidates}
"""

# ══════════ 수집 함수 ══════════
def load_archive():
    if DATA_FILE.exists():
        arc = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        n0 = len(arc["items"])
        arc["items"] = [i for i in arc["items"] if i.get("date", "") >= PURGE_BEFORE]
        if len(arc["items"]) < n0:
            print(f"잔재 정리: 개시일({PURGE_BEFORE}) 이전 항목 {n0-len(arc['items'])}건 제거")
        return arc
    return {"updated": None, "items": []}

def item_id(url, title):
    return hashlib.md5((url + title).encode()).hexdigest()[:12]

def _recent(date_str):
    return (datetime.date.today() - datetime.date.fromisoformat(date_str)).days <= RECENT_DAYS

def fetch_rss_url(url, limit=8):
    resp = requests.get(url, timeout=25, headers=UA)
    parsed = feedparser.parse(resp.content)
    rows = []
    for e in parsed.entries[:limit * 3]:
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        link = e.get("link", "").strip()
        if not title or not link:
            continue
        pub = e.get("published_parsed") or e.get("updated_parsed")
        date = datetime.date(*pub[:3]).isoformat() if pub else TODAY
        if not _recent(date):
            continue
        rows.append({"date": date, "title": title, "url": link,
                     "desc": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:300]})
        if len(rows) >= limit:
            break
    diag = f"HTTP {resp.status_code}, 피드 {len(parsed.entries)}건"
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        diag += f", 파싱오류({str(parsed.bozo_exception)[:50]})"
    return rows, diag

GNEWS_ED = {"en": ("en-US", "US", "US:en"), "ko": ("ko", "KR", "KR:ko"),
            "cn": ("zh-CN", "CN", "CN:zh-Hans"), "jp": ("ja", "JP", "JP:ja")}

def fetch_gnews(query, limit=PER_SOURCE, ed="en"):
    lang = GNEWS_ED.get(ed, GNEWS_ED["en"])
    q = urllib.parse.quote(query + f" when:{RECENT_DAYS}d")
    url = (f"https://news.google.com/rss/search?q={q}"
           f"&hl={lang[0]}&gl={lang[1]}&ceid={lang[2]}")
    return fetch_rss_url(url, limit)

def _ed_for(src):  # 소스 국가에 맞는 구글뉴스 언어판
    return {"CN": "cn", "JP": "jp", "KR": "ko"}.get(src["country"], "en")

def _date_from(url, title=""):
    """URL·제목에서 발행일 추출 (YYYY/MM/DD, YYYY-MM-DD, YYYYMMDD, YYYY년 M월 D일 등)"""
    hay = url + " " + title
    for pat in (r"(20\d{2})[/\-._](\d{1,2})[/\-._](\d{1,2})",
                r"(20\d{2})(\d{2})(\d{2})",
                r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"):
        m = re.search(pat, hay)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    return datetime.date(y, mo, d).isoformat()
                except ValueError:
                    pass
    return None

NAV_WORDS = re.compile(r"^(home|menu|login|sitemap|search|more|next|prev|이전|다음|더보기|목록|top|한국어|english|日本語|中文)$", re.I)

def fetch_html(src, limit=PER_SOURCE):
    """범용 목록 페이지 파서: 같은 도메인 링크 중 제목성 앵커 추출"""
    resp = requests.get(src["url"], timeout=25, headers=UA)
    resp.encoding = resp.apparent_encoding
    host = urllib.parse.urlparse(src["url"]).netloc
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', resp.text, re.S):
        href, inner = m.group(1), m.group(2)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if len(title) < 10 or len(title) > 160 or NAV_WORDS.match(title):
            continue
        url = urllib.parse.urljoin(src["url"], href)
        if urllib.parse.urlparse(url).netloc != host:
            continue
        if src.get("pat") and not re.search(src["pat"], url):
            continue
        if url in seen or url.rstrip("/") == src["url"].rstrip("/"):
            continue
        seen.add(url)
        guessed = _date_from(url, title)
        if guessed and not _recent(guessed):
            continue  # 발행일이 확인되는 과거 기사 차단
        out.append({"date": guessed or TODAY, "title": title, "url": url, "desc": ""})
        if len(out) >= limit:
            break
    return out, f"HTTP {resp.status_code}, 링크매칭 {len(out)}건"

def gnews_site_query(src):
    host = urllib.parse.urlparse(src["url"]).netloc if src["url"] else ""
    return src.get("q") or f"site:{host}"

def fetch_source(src):
    """소스 1곳 수집 (실패·0건 시 gnews 폴백). 반환: rows, diag"""
    if src["typ"] == "gnews":
        rows, d = fetch_gnews(src["q"], ed=_ed_for(src))
        return rows, f"구글뉴스({d})"
    try:
        if src["typ"] == "rss":
            rows, d = fetch_rss_url(src["url"])
        else:
            rows, d = fetch_html(src)
        if rows:
            return rows, d
        base_diag = d
    except Exception as ex:
        base_diag = f"{type(ex).__name__}"
    # 폴백: 구글뉴스 site 검색
    try:
        rows, d = fetch_gnews(gnews_site_query(src), ed=_ed_for(src))
        return rows, f"{base_diag} → 구글뉴스 폴백({d})"
    except Exception as ex:
        return [], f"{base_diag} → 폴백도 실패({type(ex).__name__})"

def fetch_candidates(archive):
    seen_ids = {i.get("id") for i in archive["items"]}
    seen_urls = {i.get("url") for i in archive["items"]}
    out, status = [], []
    for prio, src in enumerate(SOURCES):
        if not src["enabled"]:
            status.append(f"  - {src['source']}: 비활성({src['note']})")
            continue
        try:
            rows, diag = fetch_source(src)
        except Exception as ex:
            rows, diag = [], f"실패({type(ex).__name__}: {ex})"
        fresh = 0
        for r in rows:
            iid = item_id(r["url"], r["title"])
            if iid in seen_ids or r["url"] in seen_urls:
                continue
            out.append({**r, "id": iid, "source": src["source"],
                        "chint": src["country"], "prio": prio})
            fresh += 1
        mark = "✔" if fresh or "HTTP 200" in diag else "✘"
        status.append(f"  {mark} {src['source']}: 신규 {fresh}건 ({diag})")
    print("── 소스별 수집 상태 ──")
    print("\n".join(status))
    if len(out) > MAX_CANDIDATES:
        by_c = {}
        for c in sorted(out, key=lambda c: c["prio"]):
            by_c.setdefault(c["chint"], []).append(c)
        picked, order = [], list(by_c.keys())
        while len(picked) < MAX_CANDIDATES and any(by_c.values()):
            for cc in order:  # 국가별 순환 선택으로 균형 유지
                if by_c[cc] and len(picked) < MAX_CANDIDATES:
                    picked.append(by_c[cc].pop(0))
        print(f"[안내] 후보 {len(out)}건 → 상한 {MAX_CANDIDATES}건 절사(국가별 순환)")
        out = picked
    return out

def fetch_kiip_published():
    """KIIP 기게재 대조: RSS → 게시판 크롤 → 구글뉴스 순 폴백"""
    for url in (KIIP_TREND_RSS, KIIP_TREND_RSS.replace("https://", "http://")):
        try:
            resp = requests.get(url, timeout=25, headers=UA)
            parsed = feedparser.parse(resp.content)
            titles = [re.sub(r"\s+", " ", e.get("title", "")).strip()
                      for e in parsed.entries[:60]]
            if not titles:  # 비표준 RSS 수동 파싱
                raw = resp.content.decode(resp.apparent_encoding or "utf-8", "ignore")
                titles = [re.sub(r"\s+", " ", t).strip() for t in
                          re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)][:60]
            print(f"KIIP 대조(RSS {url.split(':')[0]}): HTTP {resp.status_code}, {len(titles)}건")
            if titles:
                return titles
        except Exception as ex:
            print(f"[warn] KIIP RSS 실패: {ex}")
    try:  # 게시판 목록 크롤
        resp = requests.get(KIIP_TREND_BOARD, timeout=25, headers=UA)
        resp.encoding = resp.apparent_encoding
        titles = []
        for m in re.finditer(r'<a[^>]+href="[^"]*view\.do[^"]*"[^>]*>(.*?)</a>', resp.text, re.S):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
            if len(t) >= 10:
                titles.append(t)
        print(f"KIIP 대조(게시판): HTTP {resp.status_code}, {len(titles)}건")
        if titles:
            return titles[:60]
    except Exception as ex:
        print(f"[warn] KIIP 게시판 실패: {ex}")
    try:  # 최후: 구글뉴스
        rows, d = fetch_gnews("site:kiip.re.kr", limit=20, ed="ko")
        print(f"KIIP 대조(구글뉴스): {len(rows)}건 ({d})")
        return [r["title"] for r in rows]
    except Exception:
        return []


# ══════════ 상세요약 생성 (팝업용) ══════════
DETAIL_PROMPT = """아래는 선별된 IP 뉴스들의 원문 본문(발췌)입니다. 각 기사에 대해 한국어로 작성하세요:
- headline: 핵심을 담은 한 줄 요약 (40자 이내)
- detail: 반드시 4개 문단, 전체 12~15문장(약 10줄 이상)의 충실한 상세 요약. 각 문단 3~4문장.
  · 1문단: 무슨 일이 있었는지(주체·행위·시점)와 그 배경·경위
  · 2문단: 핵심 내용의 구체적 설명(주요 조치·수치·대상·적용 범위·절차 등)
  · 3문단: 세부 사항과 맥락(관련 제도·기존 경과·이해관계자 입장·비교 정보 등 본문에서 확인되는 내용)
  · 4문단: 이 소식의 의미·영향·향후 일정(본문 사실 기반, 과도한 해석·추측 금지)
  원문 문장을 그대로 옮기지 말고 완전히 새로 서술하되, 전체 분량은 원문보다 훨씬 짧게 유지.
  본문 정보가 부족한 문단은 억지로 채우지 말고 확인된 사실만 서술(그 경우 3문단까지 허용).

JSON 배열로만 응답(설명·마크다운 금지): [{{"idx": 번호, "headline": "...", "detail": "문단1\n\n문단2\n\n문단3\n\n문단4"}}]

[기사 목록]
{articles}
"""

def resolve_gnews(url):
    """구글뉴스 중계 URL → 실제 언론사 원문 URL 복원"""
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(url, interval=1)
        if r.get("status") and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception:
        pass
    return url

def _fetch_body(url, limit=5000):
    """기사 본문 텍스트 추출(간이). 실패 시 빈 문자열"""
    if "news.google.com" in url:
        return ""  # 중계 페이지는 본문이 아님 (사전에 resolve 필요)
    try:
        resp = requests.get(url, timeout=15, headers=UA, allow_redirects=True)
        resp.encoding = resp.apparent_encoding
        html = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ",
                      resp.text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] if len(text) > 400 else ""
    except Exception:
        return ""

def enrich_details(items):
    """선별 기사 원문을 가져와 한줄요약·상세요약 생성"""
    from anthropic import Anthropic
    bodies = {}
    resolved = 0
    for i, it in enumerate(items):
        real = resolve_gnews(it["url"])
        if real != it["url"]:
            it["url"] = real  # 원문보기 버튼도 실제 주소로 교체
            resolved += 1
        body = _fetch_body(it["url"])
        if body:
            bodies[i] = body
    print(f"상세요약: 구글뉴스 원문복원 {resolved}건, 본문 확보 {len(bodies)}/{len(items)}건")
    if not bodies:
        return items
    client = Anthropic()
    idxs = list(bodies.keys())
    BATCH = 8
    for s in range(0, len(idxs), BATCH):
        chunk = idxs[s:s + BATCH]
        articles = "\n\n".join(f"[{i}] {items[i]['title']}\n{bodies[i]}" for i in chunk)
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=16000,
                messages=[{"role": "user",
                           "content": DETAIL_PROMPT.format(articles=articles)}])
            text = "".join(b.text for b in msg.content if b.type == "text")
            text = re.sub(r"```(json)?", "", text).strip()
            for p in json.loads(text):
                try:
                    it = items[int(p["idx"])]
                    if p.get("headline"):
                        it["headline"] = p["headline"].strip()
                    if p.get("detail"):
                        it["detail"] = p["detail"].strip()
                except (KeyError, ValueError, IndexError):
                    continue
        except Exception as ex:
            print(f"[warn] 상세요약 배치 실패({s}~): {ex}")
    done = sum(1 for it in items if it.get("detail"))
    print(f"상세요약 생성 {done}건")
    return items

# ══════════ AI 선별 ══════════
def classify(candidates, archive, kiip_titles):
    if not candidates:
        return []
    from anthropic import Anthropic
    client = Anthropic()
    recent = "\n".join(f"- {i['title']}" for i in archive["items"][:60])
    kiip = "\n".join(f"- {t}" for t in kiip_titles)
    cand_txt = "\n".join(f"[{n}] ({c['source']}) {c['title']} :: {c['desc'][:160]}"
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
    for p in picks[:DAILY_CAP + len(COUNTRIES)]:  # 국가 다양성 추가분 허용
        try:
            c = candidates[int(p["idx"])]
        except (KeyError, ValueError, IndexError):
            continue
        topic = p.get("topic", "정책·법제")
        country = p.get("country", c.get("chint", "ETC"))
        for pre, cc in FIXED_COUNTRY_PREFIX.items():  # 관청 소스 국가 고정
            if c["source"].startswith(pre):
                country = cc
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
    if os.environ.get("DRY"):
        print("[DRY] AI 선별 생략, 수집 테스트만 수행")
        return
    new_items = classify(candidates, archive, kiip_titles)
    print(f"선별 {len(new_items)}건 (기본 상한 {DAILY_CAP}건 + 국가별 보장분)")
    backlog = [i for i in archive["items"]
               if not i.get("detail")
               or i["detail"].count("\n\n") < 3
               or len(i["detail"]) < 500][:12]
    if backlog:
        print(f"소급 대상(상세요약 없는 기존 기사): {len(backlog)}건 함께 처리")
    enrich_details(new_items + backlog)
    archive["items"] = sorted(new_items + archive["items"],
                              key=lambda x: x["date"], reverse=True)
    archive["updated"] = TODAY
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(archive, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"저장 완료: 누적 {len(archive['items'])}건")

if __name__ == "__main__":
    main()
