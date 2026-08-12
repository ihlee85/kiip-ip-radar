/* ════════════════════════════════════════════════════════════════
   KIIP Global IP Radar — 관련 연구 추천 엔진 v2
   ────────────────────────────────────────────────────────────────
   기존 relStudies() 대체. 4개 신호를 가중 합산해 관련도를 산출한다.
     ① 키워드   공식키워드(가중 3.0) + 확장별칭·영문(가중 1.8)
     ② 주제     기사 topic ∈ 연구 주제태그 → +2.2
     ③ 국가     기사 country ∈ 연구 국가태그 → +1.4
     ④ 최신성   2025 +1.0 / 2024 +0.7 / 2023 +0.4 / 2022 +0.15
   ⚠ ②③④는 ①이 성립할 때만 가산한다(주제만 같다고 추천하지 않음).
   ════════════════════════════════════════════════════════════════ */

const RR = {
  /* ── 튜닝 파라미터 (운영 중 조정 지점) ───────────────────── */
  cfg: {
    W_KEY: 3.0,        // 공식키워드 1건 가중치
    W_ALIAS: 1.8,      // 확장별칭 1건 가중치
    W_TOPIC: 2.2,      // 주제 일치 가산
    W_GEO: 1.4,        // 국가 일치 가산
    W_YEAR: { 2025: 1.0, 2024: 0.7, 2023: 0.4, 2022: 0.15 },
    MAX_TERMS: 4,      // 한 연구에서 인정하는 최대 매칭어 수(편중 방지)
    MIN_SCORE: 5.5,    // 추천 최소 점수
    NEED_KEY: true,    // 공식키워드 1건 이상 필수(별칭만으로는 추천 안 함)
    TOP_N: 3,          // 최종 노출 건수
    MAX_PER_YEAR: 2,   // 같은 연도 최대 노출(연도 편중 방지)
    SCALE: 13.0        // 관련도(%) 환산 분모
  },

  /* ── 과도하게 일반적인 어휘: 단독 매칭 시 점수 대폭 감쇄 ── */
  STOP: new Set([
    '지식재산', '지식재산권', '특허', '상표', '디자인', '저작권', '산업',
    '정책', '제도', '분석', '연구', '방안', '기업', '글로벌', '주요국',
    '국내외', '동향', '보고서', '통계', '데이터', '법제', '개정', '해외',
    '심사기준', '조문', '해설서', '판례', '등록요건', '기재요건', '출원', '심사'
  ]),

  index: [],   // research-index.json 로드 결과

  /* ── 초기화 ───────────────────────────────────────────── */
  async load(url) {
    const res = await fetch(url);
    RR.index = await res.json();
    RR.index.forEach(r => { r._terms = RR._terms(r); });
    return RR.index.length;
  },
  loadFrom(arr) {                       // 인라인 배열로 주입할 때
    RR.index = arr;
    RR.index.forEach(r => { r._terms = RR._terms(r); });
    return RR.index.length;
  },

  /* ── 정규화: 대소문자·공백·중점·괄호·하이픈 제거 ────────── */
  norm(s) {
    return (s || '')
      .toLowerCase()
      .replace(/[\s·․･ㆍ・\-–—_/()[\]{}'"“”‘’,.]/g, '');
  },

  /* 짧은 라틴 약어(AI, GI, SEP, IP5 …)는 단어경계로만 매칭 */
  _isAbbr(t) { return /^[A-Za-z0-9]{2,4}$/.test(t); },

  _terms(r) {
    const out = [];
    (r.k || []).forEach(t => out.push({ t, w: RR.cfg.W_KEY, key: true }));
    (r.x || []).forEach(t => out.push({ t, w: RR.cfg.W_ALIAS, key: false }));
    return out.filter(o => RR.norm(o.t).length >= 2);
  },

  _hit(term, hayNorm, hayRaw) {
    if (RR._isAbbr(term)) {
      const re = new RegExp('(^|[^A-Za-z0-9])' + term + '([^A-Za-z0-9]|$)', 'i');
      return re.test(hayRaw);
    }
    const n = RR.norm(term);
    return n.length >= 2 && hayNorm.indexOf(n) !== -1;
  },

  /* ── 핵심: 기사 1건에 대한 추천 산출 ───────────────────── */
  recommend(news) {
    const c = RR.cfg;
    const hayRaw = [news.title, news.headline || '', news.summary || '',
                    news.detail || '', news.source || ''].join(' ');
    const hayNorm = RR.norm(hayRaw);

    const scored = [];
    for (const r of RR.index) {
      const hits = [];
      for (const o of r._terms) {
        if (!RR._hit(o.t, hayNorm, hayRaw)) continue;
        const generic = RR.STOP.has(o.t);
        hits.push({ t: o.t, w: generic ? o.w * 0.25 : o.w, key: o.key && !generic });
      }
      if (!hits.length) continue;

      hits.sort((a, b) => b.w - a.w);
      const used = hits.slice(0, c.MAX_TERMS);
      const hasKey = used.some(h => h.key);
      const topicHit = !!(news.topic && (r.tp || []).includes(news.topic));
      const geoHit = !!(news.country && (r.g || []).includes(news.country));

      /* 별칭 1건만 걸린 약한 매칭은 원칙적으로 배제.
         단 주제·국가가 모두 일치하면 맥락이 확인된 것으로 보아 허용한다. */
      if (c.NEED_KEY && !hasKey && used.length < 2 && !(topicHit && geoHit)) continue;

      let score = used.reduce((s, h) => s + h.w, 0);
      if (topicHit) score += c.W_TOPIC;
      if (geoHit) score += c.W_GEO;
      score += (c.W_YEAR[r.y] || 0);

      if (score < c.MIN_SCORE) continue;
      scored.push({
        r, score,
        pct: Math.min(99, Math.round(score / c.SCALE * 100)),
        why: used.map(h => h.t),
        topicHit, geoHit
      });
    }

    scored.sort((a, b) => b.score - a.score);

    /* 연도 편중 방지 */
    const perYear = {}, out = [];
    for (const s of scored) {
      perYear[s.r.y] = (perYear[s.r.y] || 0);
      if (perYear[s.r.y] >= c.MAX_PER_YEAR) continue;
      perYear[s.r.y]++;
      out.push(s);
      if (out.length >= c.TOP_N) break;
    }
    if (out.length) return out;

    /* ── 2차(폴백): 키워드가 안 걸리면 주제+국가가 모두 맞는 최신 연구 2건 ──
       "정확한 연결"이 아니라 "같은 영역의 참고자료"임을 tier로 구분해 표시한다. */
    return RR.index
      .filter(r => news.topic && (r.tp || []).includes(news.topic)
                && news.country && (r.g || []).includes(news.country))
      .sort((a, b) => b.y - a.y)
      .slice(0, 2)
      .map(r => ({ r, score: 0, pct: null, why: [], tier: 2, topicHit: true, geoHit: true }));
  },

  /* ── 렌더링 ───────────────────────────────────────────── */
  //  reportBase: 성과보고서 PDF를 리포에 올린 경우 경로 (예: 'reports/')
  render(list, reportBase) {
    if (!list.length) return '';
    return list.map(s => {
      const r = s.r;
      const chips = s.why.slice(0, 3)
        .map(t => `<em class="rr-chip">${t}</em>`).join('');
      const sig = [];
      if (s.topicHit) sig.push(`주제 일치`);
      if (s.geoHit) sig.push(`국가 일치`);
      const pdf = (reportBase && r.pg)
        ? ` · <a class="rr-pdf" href="${reportBase}${r.y}.pdf#page=${r.pg}" target="_blank" rel="noopener">개요 1쪽</a>`
        : '';
      const gauge = s.tier === 2
        ? `<span class="rr-pct weak">같은 영역 참고</span>`
        : `<span class="rr-score" title="관련도 ${s.pct}%"><i style="width:${s.pct}%"></i></span>
           <span class="rr-pct">${s.pct}%</span>`;
      return `
<a class="rr-card${s.tier === 2 ? ' tier2' : ''}" href="${r.u}" target="_blank" rel="noopener">
  <div class="rr-top">
    <span class="rr-cat">${r.y} ${r.c}</span>
    ${gauge}
  </div>
  <div class="rr-title">${r.t}${r.s ? `<span class="rr-sub"> — ${r.s}</span>` : ''}</div>
  <div class="rr-desc">${r.d || ''}</div>
  <div class="rr-foot">
    <span class="rr-why">${chips}${sig.length ? `<em class="rr-chip sig">${sig.join(' · ')}</em>` : ''}</span>
    <span class="rr-meta">${r.a ? r.a + ' 연구책임' : ''}${pdf}</span>
  </div>
</a>`;
    }).join('');
  }
};

/* 기존 코드와의 호환용 얇은 래퍼 — openModal() 안에서 그대로 사용 가능 */
function relStudies(d) { return RR.recommend(d); }
