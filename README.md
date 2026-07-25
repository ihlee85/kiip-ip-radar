# KIIP Global IP Radar (시범)

한국지식재산연구원용 글로벌 IP 뉴스 아카이브 — 지식재산처 `ipax-news`와 동일한
"정적 사이트 + 에이전틱 AI 루틴" 구조로, 서버·DB 없이 무료로 운영됩니다.

```
kiip-ip-radar/
├─ index.html                      # 아카이브 사이트 (그대로 열면 시연 가능)
├─ data/news.json                  # 누적 뉴스 (에이전트가 매일 갱신)
├─ collector/collect_news.py       # 수집·중복제거·분류·요약 에이전트
└─ .github/workflows/daily-news.yml# 매일 오전 6시(KST) 자동 실행
```

## 1. 즉시 시연 (설치 불필요)

`index.html`을 브라우저로 열면 예시 데이터로 바로 동작합니다.
주제 통계 밴드 클릭 = 주제 필터, 국가 칩·관청·검색·정렬 모두 작동합니다.

## 2. GitHub Pages 배포 (약 10분)

1. GitHub에 새 저장소 생성 (예: `kiip-ip-radar`) 후 이 폴더 전체 업로드
2. 저장소 **Settings → Pages → Branch: main / (root)** 선택 → 저장
3. 1~2분 후 `https://<계정명>.github.io/kiip-ip-radar/` 에서 접속 가능

## 3. 매일 자동 수집 활성화

1. [console.anthropic.com](https://console.anthropic.com)에서 API 키 발급
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY`, Value: 발급받은 키
3. **Actions 탭 → Daily IP News Collection → Run workflow** 로 첫 수동 실행 테스트
4. 이후 매일 오전 6시(KST) 자동 실행 → `data/news.json` 갱신 → 사이트 자동 반영

예상 비용: Claude API 호출 하루 1회 수준으로 **월 1~2달러 미만**, 그 외 전부 무료.

## 4. 수집원 추가·조정

`collector/collect_news.py` 상단 `FEEDS` 목록에 RSS 주소를 추가하면 됩니다.
JPO·CNIPA·KIPO처럼 RSS가 없는 곳은 공지 페이지 크롤러 함수를 추가하는 방식으로
확장합니다(2단계 작업으로 권장).

## 5. 저작권·운영 유의사항

- 뉴스 **본문을 저장·재게시하지 않고** AI 요약(2문장)과 원문 링크만 제공합니다.
- 사이트 하단에 "공식 서비스 아님 · 시범 프로젝트" 고지를 유지하세요.
- 분기별 동향보고서(AI Report) 자동 생성은 아카이브가 1~2개월 누적된 뒤
  추가하는 것을 권장합니다.
