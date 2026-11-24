// =========================================================
// 상태 관리 변수
// =========================================================
let analysisConversationId = "";
let techConversationId = "";
let currentTechCode = "";
let currentTechSummary = null;
let lastRebalanceData = null;
let isBeginnerMode = localStorage.getItem("beginnerMode") === "true";
let isProMode = localStorage.getItem("proMode") === "true";

// Chart.js 다크 테마 전역 스타일 적용
if (typeof Chart !== "undefined") {
    Chart.defaults.color = "#94a3b8";
    Chart.defaults.borderColor = "rgba(148, 163, 184, 0.14)";
}

// Chart.js 인스턴스 (재조회 시 destroy 필수)
let weightChartInstance = null;
let frontierChartInstance = null;
let priceTechChartInstance = null;
let rsiChartInstance = null;
let macdChartInstance = null;

// =========================================================
// 공용 DOM 및 유틸리티
// =========================================================
const beginnerModeBtn = document.getElementById("beginnerModeBtn");
const proModeBtn = document.getElementById("proModeBtn");
const globalErrorBox = document.getElementById("globalErrorBox");
const globalErrorList = document.getElementById("globalErrorList");

function showGlobalErrors(errors) {
    if (!errors || errors.length === 0) {
        globalErrorBox.classList.add("hidden");
        globalErrorList.innerHTML = "";
        return;
    }
    globalErrorList.innerHTML = "";
    errors.forEach((msg) => {
        const li = document.createElement("li");
        li.textContent = msg;
        globalErrorList.appendChild(li);
    });
    globalErrorBox.classList.remove("hidden");
}

function formatNumber(num, suffix = "") {
    if (num === null || num === undefined) return "-";
    return Number(num).toLocaleString("ko-KR") + suffix;
}

function formatKoreanAmount(won) {
    if (won === null || won === undefined) return "-";
    const absWon = Math.abs(won);
    const sign = won < 0 ? "-" : "";
    const eok = Math.round(absWon / 1_0000_0000);
    if (eok >= 10000) {
        const jo = Math.floor(eok / 10000);
        const remEok = eok % 10000;
        return remEok > 0
            ? `${sign}${jo.toLocaleString("ko-KR")}조 ${remEok.toLocaleString("ko-KR")}억`
            : `${sign}${jo.toLocaleString("ko-KR")}조`;
    }
    return `${sign}${eok.toLocaleString("ko-KR")}억`;
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function formatInlineMarkdown(text) {
    return text
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdownHtml(rawText) {
    const escaped = escapeHtml(rawText || "");
    const lines = escaped.split(/\r?\n/);
    const out = [];
    let inUl = false;
    let inOl = false;
    let inTable = false;
    let tableRows = [];

    function closeLists() {
        if (inUl) { out.push("</ul>"); inUl = false; }
        if (inOl) { out.push("</ol>"); inOl = false; }
    }

    function flushTable() {
        if (!inTable || tableRows.length === 0) return;
        let html = "<table><thead><tr>";
        const headers = tableRows[0];
        headers.forEach((h) => { html += `<th>${formatInlineMarkdown(h)}</th>`; });
        html += "</tr></thead><tbody>";
        for (let i = 1; i < tableRows.length; i++) {
            // 구분선 행(|---|---|) 스킵
            if (tableRows[i].every((cell) => /^:?-{2,}:?$/.test(cell.trim()))) continue;
            html += "<tr>";
            tableRows[i].forEach((c) => { html += `<td>${formatInlineMarkdown(c)}</td>`; });
            html += "</tr>";
        }
        html += "</tbody></table>";
        out.push(html);
        inTable = false;
        tableRows = [];
    }

    lines.forEach((line) => {
        const trimmed = line.trim();

        // 마크다운 테이블 행
        if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
            closeLists();
            const cells = trimmed.slice(1, -1).split("|").map((c) => c.trim());
            inTable = true;
            tableRows.push(cells);
            return;
        } else {
            flushTable();
        }

        if (!trimmed) {
            closeLists();
            return;
        }
        if (/^---+$/.test(trimmed)) {
            closeLists();
            out.push("<hr>");
            return;
        }
        if (trimmed.startsWith("#### ")) {
            closeLists();
            out.push(`<h4>${formatInlineMarkdown(trimmed.slice(5))}</h4>`);
            return;
        }
        if (trimmed.startsWith("### ")) {
            closeLists();
            out.push(`<h3>${formatInlineMarkdown(trimmed.slice(4))}</h3>`);
            return;
        }
        if (trimmed.startsWith("## ")) {
            closeLists();
            out.push(`<h3>${formatInlineMarkdown(trimmed.slice(3))}</h3>`);
            return;
        }
        if (trimmed.startsWith("# ")) {
            closeLists();
            out.push(`<h3>${formatInlineMarkdown(trimmed.slice(2))}</h3>`);
            return;
        }
        if (trimmed.startsWith("&gt; ")) {
            closeLists();
            out.push(`<blockquote>${formatInlineMarkdown(trimmed.slice(5))}</blockquote>`);
            return;
        }
        if (/^[-*] /.test(trimmed)) {
            if (inOl) { out.push("</ol>"); inOl = false; }
            if (!inUl) { out.push("<ul>"); inUl = true; }
            out.push(`<li>${formatInlineMarkdown(trimmed.slice(2))}</li>`);
            return;
        }
        if (/^\d+\.\s/.test(trimmed)) {
            if (inUl) { out.push("</ul>"); inUl = false; }
            if (!inOl) { out.push("<ol>"); inOl = true; }
            out.push(`<li>${formatInlineMarkdown(trimmed.replace(/^\d+\.\s/, ""))}</li>`);
            return;
        }

        closeLists();
        out.push(`<p>${formatInlineMarkdown(trimmed)}</p>`);
    });

    flushTable();
    closeLists();
    return out.join("");
}

function appendChatMessage(container, role, text) {
    const div = document.createElement("div");
    div.dataset.rawText = text;
    if (role === "bot") {
        div.className = "chat-msg bot md-body";
        div.innerHTML = renderMarkdownHtml(text);
    } else {
        div.className = `chat-msg ${role}`;
        div.textContent = text;
    }
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function buildChatMarkdown(container, title) {
    const msgs = container.querySelectorAll(".chat-msg:not(.loading)");
    const lines = [`# ${title}`, ""];
    msgs.forEach((m) => {
        const isUser = m.classList.contains("user");
        const raw = m.dataset.rawText || m.textContent.trim();
        lines.push(isUser ? `### 질문\n${raw}\n` : `### 리서치 답변\n${raw}\n\n---`);
    });
    return lines.join("\n");
}

async function copyChatToClipboard(container, title, btn) {
    const md = buildChatMarkdown(container, title);
    try {
        await navigator.clipboard.writeText(md);
        const orig = btn.textContent;
        btn.textContent = "복사 완료";
        setTimeout(() => { btn.textContent = orig; }, 1500);
    } catch (err) {
        alert("클립보드 복사에 실패했습니다.");
    }
}

function downloadChatMarkdown(container, title, filename) {
    const md = buildChatMarkdown(container, title);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// =========================================================
// 서버 상태 확인 (/health) & 탭 전환
// =========================================================
async function checkServerHealth() {
    try {
        const res = await fetch("/health");
        const data = await res.json();
        if (data.status !== "ok") {
            console.warn("[HEALTH] 서버 상태 이상", data);
        }
    } catch (err) {
        console.error("[ERROR] 서버 상태 확인 실패", err);
    }
}

function initTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    const tabPanels = document.querySelectorAll(".tab-panel");

    tabBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            tabBtns.forEach((b) => b.classList.remove("active"));
            tabPanels.forEach((p) => p.classList.remove("active"));

            btn.classList.add("active");
            document.getElementById(`tab-${target}`).classList.add("active");
            showGlobalErrors([]);
            console.log(`[TAB] 탭 전환 (${target})`);
        });
    });
}

// =========================================================
// 탭 1: 종목 심층분석 채팅 (collector.py + Dify 챗봇①)
// =========================================================
const analysisInitForm = document.getElementById("analysisInitForm");
const analysisCodeInput = document.getElementById("analysisCode");
const analysisAvgPriceInput = document.getElementById("analysisAvgPrice");
const analysisQtyInput = document.getElementById("analysisQty");
const useToolCheck = document.getElementById("useToolCheck");
const analysisFetchBtn = document.getElementById("analysisFetchBtn");
const analysisLoading = document.getElementById("analysisLoading");
const analysisLoadingText = document.getElementById("analysisLoadingText");

const analysisEvidenceCard = document.getElementById("analysisEvidenceCard");
const toggleEvidenceBtn = document.getElementById("toggleEvidenceBtn");
const evidenceBody = document.getElementById("evidenceBody");
const evUserPositionBox = document.getElementById("evUserPositionBox");
const evUserPositionGrid = document.getElementById("evUserPositionGrid");
const evCodeBadge = document.getElementById("evCodeBadge");
const evStockName = document.getElementById("evStockName");
const evCurrentPrice = document.getElementById("evCurrentPrice");
const evChangeRate = document.getElementById("evChangeRate");
const evMarketConsensusGrid = document.getElementById("evMarketConsensusGrid");
const evQuoteGrid = document.getElementById("evQuoteGrid");
const evInvestGrid = document.getElementById("evInvestGrid");
const evProValuationGrid = document.getElementById("evProValuationGrid");
const evFinancialsTable = document.getElementById("evFinancialsTable");
const evQuarterTable = document.getElementById("evQuarterTable");
const evTrendsTable = document.getElementById("evTrendsTable");
const evEzinitBox = document.getElementById("evEzinitBox");
const evExtraBox = document.getElementById("evExtraBox");
const evNewsList = document.getElementById("evNewsList");

const analysisChatBox = document.getElementById("analysisChatBox");
const analysisChatForm = document.getElementById("analysisChatForm");
const analysisQuestionInput = document.getElementById("analysisQuestion");
const analysisSendBtn = document.getElementById("analysisSendBtn");
const resetAnalysisChatBtn = document.getElementById("resetAnalysisChatBtn");
const copyAnalysisBtn = document.getElementById("copyAnalysisBtn");
const saveAnalysisBtn = document.getElementById("saveAnalysisBtn");

function renderMetricGrid(container, items) {
    container.innerHTML = "";
    items.forEach(({ label, value }) => {
        const div = document.createElement("div");
        div.className = "metric-item";
        const labelEl = document.createElement("div");
        labelEl.className = "metric-label";
        labelEl.textContent = label;
        const valEl = document.createElement("div");
        valEl.className = "metric-value";
        valEl.textContent = value;
        div.appendChild(labelEl);
        div.appendChild(valEl);
        container.appendChild(div);
    });
}

function renderUserPosition(userPos) {
    if (!userPos) {
        evUserPositionBox.classList.add("hidden");
        return;
    }
    const items = [
        { label: "내 평단가", value: formatNumber(userPos.avg_price, "원") },
        { label: "현재가", value: formatNumber(userPos.current_price, "원") },
        { label: "현재 손익률", value: `${userPos.return_pct > 0 ? "+" : ""}${userPos.return_pct}%` },
    ];
    if (userPos.quantity) {
        items.push({ label: "보유 수량", value: formatNumber(userPos.quantity, "주") });
    }
    if (userPos.pnl !== null && userPos.pnl !== undefined) {
        items.push({ label: "평가 손익", value: `${userPos.pnl > 0 ? "+" : ""}${formatNumber(userPos.pnl, "원")}` });
    }
    renderMetricGrid(evUserPositionGrid, items);
    evUserPositionBox.classList.remove("hidden");
}

function renderEvidenceDashboard(evidence, useTool, userPos = null) {
    if (!useTool || !evidence) {
        analysisEvidenceCard.classList.add("hidden");
        return;
    }
    renderUserPosition(userPos);

    const snap = evidence.snapshot || {};
    evCodeBadge.textContent = evidence.code || "-";
    evStockName.textContent = evidence.name || "종목명 미확인";
    evCurrentPrice.textContent = formatNumber(snap.current_price, "원");

    const rate = snap.change_rate;
    if (rate !== null && rate !== undefined) {
        const sign = rate > 0 ? "+" : "";
        evChangeRate.textContent = `전일대비 ${sign}${rate}%`;
        evChangeRate.className = "change-rate " + (rate > 0 ? "up" : rate < 0 ? "down" : "flat");
    } else {
        evChangeRate.textContent = "-";
        evChangeRate.className = "change-rate flat";
    }

    // 0구역: 시장 지수 & 증권사 목표주가
    const mkt = evidence.market_indices || {};
    const cns = evidence.consensus || {};
    const kospi = mkt.KOSPI || {};
    const kosdaq = mkt.KOSDAQ || {};
    renderMetricGrid(evMarketConsensusGrid, [
        { label: "코스피 (KOSPI)", value: kospi.value ? `${formatNumber(kospi.value)} (${kospi.change_rate > 0 ? "+" : ""}${kospi.change_rate}%)` : "-" },
        { label: "코스닥 (KOSDAQ)", value: kosdaq.value ? `${formatNumber(kosdaq.value)} (${kosdaq.change_rate > 0 ? "+" : ""}${kosdaq.change_rate}%)` : "-" },
        { label: "증권사 평균 목표주가", value: formatNumber(cns.target_price, "원") },
        { label: "투자의견 (5점 만점)", value: cns.recomm_score ? `${cns.recomm_score}점` : "-" },
    ]);

    // 1구역: 당일 시세
    renderMetricGrid(evQuoteGrid, [
        { label: "전일 종가", value: formatNumber(snap.prev_close, "원") },
        { label: "시가", value: formatNumber(snap.open_price, "원") },
        { label: "고가", value: formatNumber(snap.high_price, "원") },
        { label: "저가", value: formatNumber(snap.low_price, "원") },
        { label: "거래량", value: formatNumber(snap.volume, "주") },
        { label: "거래대금", value: formatKoreanAmount(snap.trading_value) },
    ]);

    // 2구역: 투자정보
    renderMetricGrid(evInvestGrid, [
        { label: "시가총액", value: formatKoreanAmount(snap.market_cap) },
        { label: "외인소진율", value: formatNumber(snap.foreign_rate, "%") },
        { label: "52주 최고", value: formatNumber(snap.week52_high, "원") },
        { label: "52주 최저", value: formatNumber(snap.week52_low, "원") },
        { label: "PER", value: formatNumber(snap.per, "배") },
        { label: "추정 PER", value: formatNumber(snap.cns_per, "배") },
        { label: "EPS", value: formatNumber(snap.eps, "원") },
        { label: "추정 EPS", value: formatNumber(snap.cns_eps, "원") },
        { label: "PBR", value: formatNumber(snap.pbr, "배") },
        { label: "BPS", value: formatNumber(snap.bps, "원") },
        { label: "배당수익률", value: formatNumber(snap.dividend_yield, "%") },
        { label: "주당배당금", value: formatNumber(snap.dividend, "원") },
    ]);

    // 전문가 모드 전용: 파생 밸류에이션 & 괴리율 계산
    const currP = snap.current_price;
    const targetP = cns.target_price;
    const upsidePct = (currP && targetP) ? (((targetP - currP) / currP) * 100).toFixed(2) : null;
    const deratingPct = (snap.per && snap.cns_per) ? (((snap.per - snap.cns_per) / snap.per) * 100).toFixed(2) : null;
    const dd52Pct = (currP && snap.week52_high) ? (((currP - snap.week52_high) / snap.week52_high) * 100).toFixed(2) : null;
    const pos52Pct = (currP && snap.week52_high && snap.week52_low && snap.week52_high !== snap.week52_low)
        ? (((currP - snap.week52_low) / (snap.week52_high - snap.week52_low)) * 100).toFixed(1)
        : null;

    renderMetricGrid(evProValuationGrid, [
        { label: "목표주가 괴리율 (Upside)", value: upsidePct !== null ? `${upsidePct > 0 ? "+" : ""}${upsidePct}%` : "-" },
        { label: "Fwd PER 할인율 (디레이팅)", value: deratingPct !== null ? `${deratingPct}%` : "-" },
        { label: "52주 고점 대비 낙폭", value: dd52Pct !== null ? `${dd52Pct}%` : "-" },
        { label: "52주 밴드 내 위치", value: pos52Pct !== null ? `${pos52Pct}%` : "-" },
    ]);

    // 3구역: 연간 & 분기 기업실적분석 테이블 (공용 렌더러)
    function renderFinanceTableElement(tableEl, finList) {
        const thead = tableEl.querySelector("thead");
        const tbody = tableEl.querySelector("tbody");
        thead.innerHTML = "";
        tbody.innerHTML = "";

        if (!finList || finList.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5">수집된 실적 데이터가 없습니다.</td></tr>';
            return;
        }

        const headerRow = document.createElement("tr");
        const mTh = document.createElement("th");
        mTh.textContent = "주요 재무지표";
        headerRow.appendChild(mTh);

        finList.forEach((f) => {
            const th = document.createElement("th");
            if (f.is_consensus) {
                th.className = "consensus-col";
                th.innerHTML = `${escapeHtml(f.period)} <span class="consensus-badge">예상(E)</span>`;
            } else {
                th.textContent = f.period;
            }
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);

        const rows = [
            { key: "revenue", label: "매출액 (원)", formatter: formatKoreanAmount },
            { key: "operating_income", label: "영업이익 (원)", formatter: formatKoreanAmount },
            { key: "net_income", label: "당기순이익 (원)", formatter: formatKoreanAmount },
            { key: "operating_margin", label: "영업이익률 (%)", formatter: (v) => formatNumber(v, "%") },
            { key: "roe", label: "ROE (%)", formatter: (v) => formatNumber(v, "%") },
            { key: "debt_ratio", label: "부채비율 (%)", formatter: (v) => formatNumber(v, "%") },
            { key: "per", label: "PER (배)", formatter: (v) => formatNumber(v, "배") },
            { key: "pbr", label: "PBR (배)", formatter: (v) => formatNumber(v, "배") },
        ];

        rows.forEach(({ key, label, formatter }) => {
            const tr = document.createElement("tr");
            const lTd = document.createElement("td");
            lTd.textContent = label;
            tr.appendChild(lTd);

            finList.forEach((f) => {
                const td = document.createElement("td");
                const val = f[key];
                if (f.is_consensus) td.classList.add("consensus-col");
                if (typeof val === "number" && val < 0) td.classList.add("neg-val");
                td.textContent = formatter(val);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    }

    renderFinanceTableElement(evFinancialsTable, evidence.financials || []);
    renderFinanceTableElement(evQuarterTable, evidence.quarter_financials || []);

    // 4구역: 최근 5거래일 수급 동향
    const trendsBody = evTrendsTable.querySelector("tbody");
    trendsBody.innerHTML = "";
    const trends = evidence.investor_trends || [];
    if (trends.length === 0) {
        trendsBody.innerHTML = '<tr><td colspan="6">수급 동향 데이터가 없습니다.</td></tr>';
    } else {
        trends.forEach((t) => {
            const tr = document.createElement("tr");
            const fSign = (t.foreigner_net || 0) > 0 ? "+" : "";
            const oSign = (t.organ_net || 0) > 0 ? "+" : "";
            const iSign = (t.individual_net || 0) > 0 ? "+" : "";
            tr.innerHTML = `
                <td>${escapeHtml(t.date || "-")}</td>
                <td>${formatNumber(t.close, "원")}</td>
                <td class="${(t.foreigner_net || 0) < 0 ? "neg-val" : ""}">${fSign}${formatNumber(t.foreigner_net)}</td>
                <td class="${(t.organ_net || 0) < 0 ? "neg-val" : ""}">${oSign}${formatNumber(t.organ_net)}</td>
                <td class="${(t.individual_net || 0) < 0 ? "neg-val" : ""}">${iSign}${formatNumber(t.individual_net)}</td>
                <td>${t.foreigner_ratio != null ? t.foreigner_ratio + "%" : "-"}</td>
            `;
            trendsBody.appendChild(tr);
        });
    }

    // 5구역: 시장 상대강도 요약 & OpenDART 공식 공시
    evEzinitBox.innerHTML = "";
    const ez = evidence.ezinit_analysis;
    const dartList = evidence.dart_disclosures || [];

    if (!ez && dartList.length === 0) {
        evEzinitBox.innerHTML = '<p class="muted-text">이 종목에 대한 최근 수급/공시 요약 데이터가 없습니다.</p>';
    } else {
        if (ez && ez.summary) {
            const sumTitle = document.createElement("h4");
            sumTitle.textContent = "시장 대비 상대강도 & 수급 요약";
            evEzinitBox.appendChild(sumTitle);

            const sumEl = document.createElement("p");
            sumEl.style.marginTop = "6px";
            sumEl.textContent = ez.summary;
            evEzinitBox.appendChild(sumEl);
        }

        const disclosuresToShow = dartList.length > 0 ? dartList : (ez?.disclosures || []);
        if (disclosuresToShow.length > 0) {
            const discTitle = document.createElement("h4");
            discTitle.style.marginTop = "14px";
            discTitle.textContent = dartList.length > 0
                ? "금융감독원 OpenDART 공식 공시 (최근 90일)"
                : "최근 DART 주요 공시";
            evEzinitBox.appendChild(discTitle);

            disclosuresToShow.forEach((d) => {
                const p = document.createElement("p");
                p.style.marginTop = "4px";
                if (d.url) {
                    p.innerHTML = `• [${escapeHtml(d.date)}] <a href="${escapeHtml(d.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(d.title)} ↗</a> (${escapeHtml(d.submitter || "")})`;
                } else {
                    p.textContent = `• [${d.date}] (${d.category || ""}) ${d.title}`;
                }
                evEzinitBox.appendChild(p);
            });
        }
    }

    // 6구역: 동일업종 비교 & 증권사 최신 리포트
    evExtraBox.innerHTML = "";
    const peers = evidence.peers || [];
    const researches = evidence.researches || [];

    const peerBlock = document.createElement("div");
    peerBlock.className = "evidence-block";
    peerBlock.innerHTML = "<h4>동종 업계 시세</h4>";
    if (peers.length === 0) {
        peerBlock.innerHTML += '<p class="muted-text">비교 종목 정보 없음</p>';
    } else {
        peers.forEach((p) => {
            const item = document.createElement("p");
            item.textContent = `• ${p.name}(${p.code}): ${formatNumber(p.close, "원")} (${p.change_rate > 0 ? "+" : ""}${p.change_rate ?? "-"}%)`;
            peerBlock.appendChild(item);
        });
    }
    evExtraBox.appendChild(peerBlock);

    const resBlock = document.createElement("div");
    resBlock.className = "evidence-block";
    resBlock.innerHTML = "<h4>증권사 최신 리포트</h4>";
    if (researches.length === 0) {
        resBlock.innerHTML += '<p class="muted-text">최신 리포트 없음</p>';
    } else {
        researches.forEach((r) => {
            const item = document.createElement("p");
            item.textContent = `• [${r.date}] ${r.firm} — ${r.title}`;
            resBlock.appendChild(item);
        });
    }
    evExtraBox.appendChild(resBlock);

    // 뉴스 리스트
    evNewsList.innerHTML = "";
    const news = evidence.news || [];
    if (news.length === 0) {
        const li = document.createElement("li");
        li.className = "news-item";
        li.textContent = "수집된 최신 뉴스가 없습니다.";
        evNewsList.appendChild(li);
    } else {
        news.forEach((item) => {
            const li = document.createElement("li");
            li.className = "news-item";
            const a = document.createElement("a");
            a.className = "news-title";
            a.href = item.url || "#";
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.textContent = item.title || "제목 없음";
            const meta = document.createElement("div");
            meta.className = "news-meta";
            meta.textContent = `${item.source || "언론사"} · ${item.published || ""}`;
            li.appendChild(a);
            li.appendChild(meta);
            if (item.description) {
                const desc = document.createElement("p");
                desc.className = "news-desc";
                desc.textContent = item.description;
                li.appendChild(desc);
            }
            evNewsList.appendChild(li);
        });
    }

    evidenceBody.classList.remove("hidden");
    analysisEvidenceCard.classList.remove("hidden");
}

// 1단계: 종목 조회(/fetch) -> 대시보드 즉시 표시 -> 2단계: 전문가 1차 리포트 생성(/api/chat/analysis)
analysisInitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = analysisCodeInput.value.trim();
    const avgPrice = analysisAvgPriceInput.value.trim();
    const quantity = analysisQtyInput.value.trim();
    const useTool = useToolCheck.checked;

    showGlobalErrors([]);
    analysisConversationId = "";
    analysisEvidenceCard.classList.add("hidden");
    analysisLoadingText.textContent = `'${code}' 데이터 수집 중...`;
    analysisLoading.classList.remove("hidden");
    analysisFetchBtn.disabled = true;

    try {
        let fetchedEvidence = null;

        if (useTool) {
            const fetchRes = await fetch("/fetch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code }),
            });
            const fetchData = await fetchRes.json();
            if (!fetchRes.ok) {
                showGlobalErrors(fetchData.errors || ["종목 데이터 수집에 실패했습니다."]);
                return;
            }
            fetchedEvidence = fetchData;

            // 평단가/수량 화면 미리 계산
            let previewPos = null;
            const currP = fetchData.snapshot?.current_price;
            const avgNum = Number(avgPrice);
            const qtyNum = Number(quantity);
            if (currP && avgNum > 0) {
                previewPos = {
                    avg_price: avgNum,
                    current_price: currP,
                    return_pct: Number((((currP - avgNum) / avgNum) * 100).toFixed(2)),
                    quantity: qtyNum > 0 ? qtyNum : null,
                    pnl: qtyNum > 0 ? Math.round((currP - avgNum) * qtyNum) : null,
                };
            }
            renderEvidenceDashboard(fetchedEvidence, true, previewPos);
            if (fetchData.errors && fetchData.errors.length > 0) {
                showGlobalErrors(fetchData.errors);
            }
        }

        analysisLoadingText.textContent = `'${code}' 데이터 분석 중...`;

        const res = await fetch("/api/chat/analysis", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action: "init",
                code,
                avg_price: avgPrice,
                quantity,
                use_tool: useTool,
                evidence: fetchedEvidence,
                beginner_mode: isBeginnerMode,
                pro_mode: isProMode,
            }),
        });

        let data;
        try {
            data = await res.json();
        } catch {
            showGlobalErrors(["AI 응답 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."]);
            return;
        }

        if (!res.ok) {
            showGlobalErrors([data.error || "종목 분석에 실패했습니다."]);
            console.error("[ERROR] 탭1 조회·분석 오류", data.error);
            return;
        }

        if (data.user_position) {
            renderUserPosition(data.user_position);
        }

        analysisConversationId = data.conversation_id || "";
        analysisChatBox.innerHTML = "";
        appendChatMessage(
            analysisChatBox,
            "user",
            `[${code} / ${useTool ? "실시간 데이터" : "데이터 미연동"}] 종목 조회 및 심층 분석`
        );
        appendChatMessage(analysisChatBox, "bot", data.answer || "(빈 응답)");

        console.log(`[ANALYSIS] 탭1 종목 조회 및 1차 분석 완료 (code=${code}, conv=${analysisConversationId})`);
    } catch (err) {
        showGlobalErrors(["서버와 통신하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."]);
        console.error("[ERROR] 탭1 조회 통신 오류", err);
    } finally {
        analysisLoading.classList.add("hidden");
        analysisFetchBtn.disabled = false;
    }
});

// 2단계: 열린 Dify 대화를 기반으로 후속 질문 이어가기
analysisChatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = analysisCodeInput.value.trim();
    const question = analysisQuestionInput.value.trim();
    const useTool = useToolCheck.checked;

    if (!question) return;

    showGlobalErrors([]);
    appendChatMessage(analysisChatBox, "user", question);
    analysisQuestionInput.value = "";

    const loadingMsg = appendChatMessage(analysisChatBox, "bot loading", "답변을 정리하고 있습니다...");
    analysisSendBtn.disabled = true;

    try {
        const res = await fetch("/api/chat/analysis", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action: "chat",
                code,
                question,
                use_tool: useTool,
                conversation_id: analysisConversationId,
                beginner_mode: isBeginnerMode,
                pro_mode: isProMode,
            }),
        });
        const data = await res.json();
        loadingMsg.remove();

        if (!res.ok) {
            showGlobalErrors([data.error || "답변 생성에 실패했습니다."]);
            appendChatMessage(analysisChatBox, "bot", `오류: ${data.error || "요청 실패"}`);
            console.error("[ERROR] 탭1 후속 채팅 오류", data.error);
            return;
        }

        analysisConversationId = data.conversation_id || analysisConversationId;
        if (data.evidence) {
            renderEvidenceDashboard(data.evidence, data.use_tool, data.user_position);
        }
        appendChatMessage(analysisChatBox, "bot", data.answer || "(빈 응답)");
        console.log(`[ANALYSIS] 탭1 후속 채팅 응답 수신 (conv=${analysisConversationId})`);
    } catch (err) {
        loadingMsg.remove();
        showGlobalErrors(["서버 통신 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭1 채팅 통신 오류", err);
    } finally {
        analysisSendBtn.disabled = false;
    }
});

toggleEvidenceBtn.addEventListener("click", () => {
    evidenceBody.classList.toggle("hidden");
});

resetAnalysisChatBtn.addEventListener("click", () => {
    analysisConversationId = "";
    analysisEvidenceCard.classList.add("hidden");
    analysisChatBox.innerHTML =
        '<div class="chat-msg bot">대화가 초기화되었습니다. 종목코드를 입력하고 <strong>종목 조회 및 분석</strong> 버튼을 눌러주세요.</div>';
});

document.querySelectorAll(".analysis-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        analysisCodeInput.value = btn.dataset.code;
    });
});

document.querySelectorAll(".analysis-q-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        analysisQuestionInput.value = btn.dataset.q;
    });
});

copyAnalysisBtn.addEventListener("click", () => {
    const code = analysisCodeInput.value.trim() || "stock";
    copyChatToClipboard(analysisChatBox, `종목 심층분석 (${code})`, copyAnalysisBtn);
});

saveAnalysisBtn.addEventListener("click", () => {
    const code = analysisCodeInput.value.trim() || "stock";
    downloadChatMarkdown(analysisChatBox, `종목 심층분석 (${code})`, `analysis_${code}.md`);
});

// =========================================================
// 탭 2: 리밸런싱 도우미 (Dify 없음 · 순수 Python 계산)
// =========================================================
const rebalanceForm = document.getElementById("rebalanceForm");
const rebalanceTickersInput = document.getElementById("rebalanceTickers");
const rebalanceCapitalInput = document.getElementById("rebalanceCapital");
const rebalanceBtn = document.getElementById("rebalanceBtn");
const rebalanceLoading = document.getElementById("rebalanceLoading");
const rebalanceResult = document.getElementById("rebalanceResult");
const rebalanceSummaryGrid = document.getElementById("rebalanceSummaryGrid");
const assetStatsTableBody = document.querySelector("#assetStatsTable tbody");
const corrMatrixTable = document.getElementById("corrMatrixTable");

function renderRebalanceCharts(data) {
    const optimal = data.optimal_portfolio;
    const weights = optimal.weights;
    const labels = Object.keys(weights);
    const pctValues = labels.map((k) => Number((weights[k] * 100).toFixed(2)));

    if (weightChartInstance) weightChartInstance.destroy();
    const wCtx = document.getElementById("weightChart").getContext("2d");
    weightChartInstance = new Chart(wCtx, {
        type: "doughnut",
        data: {
            labels,
            datasets: [{
                data: pctValues,
                backgroundColor: ["#2563eb", "#7c3aed", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"],
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "bottom" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.label}: ${ctx.parsed}%`,
                    },
                },
            },
        },
    });

    if (frontierChartInstance) frontierChartInstance.destroy();
    const fCtx = document.getElementById("frontierChart").getContext("2d");
    const cloudPoints = (data.frontier_points || []).map((pt) => ({
        x: pt.volatility,
        y: pt.return,
    }));

    frontierChartInstance = new Chart(fCtx, {
        type: "scatter",
        data: {
            datasets: [
                {
                    label: "몬테카를로 포트폴리오",
                    data: cloudPoints,
                    backgroundColor: "rgba(37, 99, 235, 0.35)",
                    pointRadius: 3,
                },
                {
                    label: "최적 포트폴리오 (최대 샤프)",
                    data: [{ x: optimal.volatility, y: optimal.expected_return }],
                    backgroundColor: "#dc2626",
                    pointRadius: 8,
                    pointStyle: "star",
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { title: { display: true, text: "연율화 변동성 (%)" } },
                y: { title: { display: true, text: "기대 수익률 (%)" } },
            },
        },
    });
}

function renderAssetStatsTable(data) {
    if (!data || !data.optimal_portfolio) return;
    const opt = data.optimal_portfolio;
    const totalCapital = Number(rebalanceCapitalInput.value) || 0;

    assetStatsTableBody.innerHTML = "";
    data.tickers.forEach((code) => {
        const stat = data.asset_stats[code] || {};
        const weight = opt.weights[code] || 0;
        const weightPct = (weight * 100).toFixed(2);
        const latestPrice = stat.latest_price || 0;
        const targetAmt = Math.round(totalCapital * weight);
        const targetShares = latestPrice > 0 ? Math.floor(targetAmt / latestPrice) : 0;

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${escapeHtml(code)}</td>
            <td>${latestPrice ? formatNumber(latestPrice, "원") : "-"}</td>
            <td><strong>${weightPct}%</strong></td>
            <td><strong>${totalCapital > 0 ? formatNumber(targetAmt, "원") : "-"}</strong></td>
            <td>${totalCapital > 0 && latestPrice > 0 ? formatNumber(targetShares, "주") : "-"}</td>
            <td>${stat.annual_return ?? "-"}%</td>
            <td>${stat.volatility ?? "-"}%</td>
            <td>${stat.sharpe_ratio ?? "-"}</td>
            <td class="neg-val">${stat.mdd ?? "-"}%</td>
        `;
        assetStatsTableBody.appendChild(tr);
    });
}

rebalanceCapitalInput.addEventListener("input", () => {
    if (lastRebalanceData) {
        renderAssetStatsTable(lastRebalanceData);
    }
});

rebalanceForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = rebalanceTickersInput.value.trim();
    const tickers = raw.split(",").map((s) => s.trim()).filter(Boolean);

    showGlobalErrors([]);
    rebalanceResult.classList.add("hidden");
    rebalanceLoading.classList.remove("hidden");
    rebalanceBtn.disabled = true;

    try {
        const res = await fetch("/api/rebalance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickers }),
        });
        const data = await res.json();

        if (!res.ok) {
            showGlobalErrors(data.errors || ["리밸런싱 계산에 실패했습니다."]);
            console.error("[ERROR] 탭2 리밸런싱 오류", data.errors);
            return;
        }

        if (data.errors && data.errors.length > 0) {
            showGlobalErrors(data.errors);
        }

        const opt = data.optimal_portfolio;
        rebalanceSummaryGrid.innerHTML = "";
        [
            { label: "기대 수익률 (연율)", value: `${opt.expected_return}%` },
            { label: "변동성 (연율)", value: `${opt.volatility}%` },
            { label: "샤프지수 (Sharpe)", value: `${opt.sharpe_ratio}` },
            { label: "최대 낙폭 (MDD)", value: `${opt.mdd}%` },
            { label: "분석 거래일수", value: `${data.period_days}일` },
        ].forEach(({ label, value }) => {
            const div = document.createElement("div");
            div.className = "metric-item";
            div.innerHTML = `<div class="metric-label">${label}</div><div class="metric-value">${value}</div>`;
            rebalanceSummaryGrid.appendChild(div);
        });

        lastRebalanceData = data;
        renderAssetStatsTable(data);

        // 전문가 모드: 상관계수 행렬 렌더링
        const corr = data.correlation_matrix || {};
        const cHead = corrMatrixTable.querySelector("thead");
        const cBody = corrMatrixTable.querySelector("tbody");
        cHead.innerHTML = "";
        cBody.innerHTML = "";
        if (data.tickers && Object.keys(corr).length > 0) {
            const hRow = document.createElement("tr");
            hRow.innerHTML = "<th>종목</th>" + data.tickers.map((t) => `<th>${escapeHtml(t)}</th>`).join("");
            cHead.appendChild(hRow);

            data.tickers.forEach((rowT) => {
                const tr = document.createElement("tr");
                let cells = `<td>${escapeHtml(rowT)}</td>`;
                data.tickers.forEach((colT) => {
                    const val = corr[rowT]?.[colT];
                    cells += `<td>${val !== undefined ? val.toFixed(2) : "-"}</td>`;
                });
                tr.innerHTML = cells;
                cBody.appendChild(tr);
            });
        }

        renderRebalanceCharts(data);
        rebalanceResult.classList.remove("hidden");
        console.log(`[REBALANCE] 탭2 리밸런싱 계산 완료 (최적 샤프지수=${opt.sharpe_ratio})`);
    } catch (err) {
        showGlobalErrors(["리밸런싱 요청 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭2 통신 오류", err);
    } finally {
        rebalanceLoading.classList.add("hidden");
        rebalanceBtn.disabled = false;
    }
});

document.querySelectorAll(".rebalance-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        rebalanceTickersInput.value = btn.dataset.tickers;
    });
});

// =========================================================
// 탭 3: 기술지표 차트 + 채팅 (FDR 계산 + Dify 챗봇③)
// =========================================================
const techSearchForm = document.getElementById("techSearchForm");
const techCodeInput = document.getElementById("techCode");
const techSearchBtn = document.getElementById("techSearchBtn");
const techLoading = document.getElementById("techLoading");
const techResult = document.getElementById("techResult");
const techSummaryGrid = document.getElementById("techSummaryGrid");
const techProGrid = document.getElementById("techProGrid");

const techChatBox = document.getElementById("techChatBox");
const techChatForm = document.getElementById("techChatForm");
const techQuestionInput = document.getElementById("techQuestion");
const techSendBtn = document.getElementById("techSendBtn");
const resetTechChatBtn = document.getElementById("resetTechChatBtn");
const copyTechBtn = document.getElementById("copyTechBtn");
const saveTechBtn = document.getElementById("saveTechBtn");

function renderTechCharts(data) {
    const ind = data.indicators;

    if (priceTechChartInstance) priceTechChartInstance.destroy();
    const pCtx = document.getElementById("priceTechChart").getContext("2d");
    priceTechChartInstance = new Chart(pCtx, {
        type: "line",
        data: {
            labels: data.dates,
            datasets: [
                { label: "종가", data: data.close, borderColor: "#f8fafc", borderWidth: 2.2, pointRadius: 0 },
                { label: "MA5", data: ind.ma5, borderColor: "#f87171", borderWidth: 1.5, pointRadius: 0 },
                { label: "MA20", data: ind.ma20, borderColor: "#fbbf24", borderWidth: 1.5, pointRadius: 0 },
                { label: "MA60", data: ind.ma60, borderColor: "#34d399", borderWidth: 1.5, pointRadius: 0 },
                { label: "볼린저 상단", data: ind.bb_upper, borderColor: "#64748b", borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
                { label: "볼린저 하단", data: ind.bb_lower, borderColor: "#64748b", borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { position: "bottom" } },
        },
    });

    if (rsiChartInstance) rsiChartInstance.destroy();
    const rCtx = document.getElementById("rsiChart").getContext("2d");
    rsiChartInstance = new Chart(rCtx, {
        type: "line",
        data: {
            labels: data.dates,
            datasets: [
                { label: "RSI (14)", data: ind.rsi14, borderColor: "#7c3aed", borderWidth: 2, pointRadius: 0 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: 0, max: 100 },
            },
            plugins: { legend: { position: "bottom" } },
        },
    });

    if (macdChartInstance) macdChartInstance.destroy();
    const mCtx = document.getElementById("macdChart").getContext("2d");
    macdChartInstance = new Chart(mCtx, {
        type: "line",
        data: {
            labels: data.dates,
            datasets: [
                { label: "MACD (12,26)", data: ind.macd, borderColor: "#2563eb", borderWidth: 2, pointRadius: 0 },
                { label: "Signal (9)", data: ind.macd_signal, borderColor: "#f59e0b", borderWidth: 1.8, pointRadius: 0 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { position: "bottom" } },
        },
    });
}

techSearchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = techCodeInput.value.trim();

    showGlobalErrors([]);
    techResult.classList.add("hidden");
    techLoading.classList.remove("hidden");
    techSearchBtn.disabled = true;

    try {
        const res = await fetch("/api/technical", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code }),
        });
        const data = await res.json();

        if (!res.ok) {
            showGlobalErrors(data.errors || ["기술지표 계산에 실패했습니다."]);
            console.error("[ERROR] 탭3 기술지표 오류", data.errors);
            return;
        }

        currentTechCode = data.code;
        currentTechSummary = data.latest_summary;
        techConversationId = "";

        const s = currentTechSummary;
        techSummaryGrid.innerHTML = "";
        [
            { label: `종가 (${s.date || "-"})`, value: formatNumber(s.close, "원") },
            { label: "5일선 (MA5)", value: formatNumber(s.ma5, "원") },
            { label: "20일선 (MA20)", value: formatNumber(s.ma20, "원") },
            { label: "60일선 (MA60)", value: formatNumber(s.ma60, "원") },
            { label: "RSI (14)", value: formatNumber(s.rsi14) },
            { label: "MACD / Signal", value: `${formatNumber(s.macd)} / ${formatNumber(s.macd_signal)}` },
            { label: "볼린저 상단", value: formatNumber(s.bb_upper, "원") },
            { label: "볼린저 하단", value: formatNumber(s.bb_lower, "원") },
        ].forEach(({ label, value }) => {
            const div = document.createElement("div");
            div.className = "metric-item";
            div.innerHTML = `<div class="metric-label">${label}</div><div class="metric-value">${value}</div>`;
            techSummaryGrid.appendChild(div);
        });

        // 전문가 모드: 정량 모멘텀 & 변동성 수축 지표
        renderMetricGrid(techProGrid, [
            { label: "20일선 이격도", value: s.disparity_20 != null ? `${s.disparity_20}%` : "-" },
            { label: "볼린저 %B (밴드 위치)", value: s.bb_pct_b != null ? `${s.bb_pct_b}%` : "-" },
            { label: "볼린저 밴드 폭 (스퀴즈)", value: s.bb_width != null ? `${s.bb_width}%` : "-" },
            { label: "MACD 히스토그램", value: s.macd_hist != null ? `${s.macd_hist > 0 ? "+" : ""}${formatNumber(s.macd_hist)}` : "-" },
        ]);

        renderTechCharts(data);
        techResult.classList.remove("hidden");
        console.log(`[TECHNICAL] 탭3 기술지표 계산 및 차트 렌더링 완료 (code=${code}, rsi14=${s.rsi14})`);
    } catch (err) {
        showGlobalErrors(["기술지표 요청 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭3 통신 오류", err);
    } finally {
        techLoading.classList.add("hidden");
        techSearchBtn.disabled = false;
    }
});

techChatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentTechCode || !currentTechSummary) {
        showGlobalErrors(["먼저 종목코드를 입력해 차트와 지표를 계산해 주세요."]);
        return;
    }

    const question = techQuestionInput.value.trim() || "현재 계산된 기술지표 수치를 초보자가 이해하기 쉽게 설명해줘";
    showGlobalErrors([]);
    appendChatMessage(techChatBox, "user", question);
    techQuestionInput.value = "";

    const loadingMsg = appendChatMessage(techChatBox, "bot loading", "지표 데이터를 분석하고 있습니다...");
    techSendBtn.disabled = true;

    try {
        const res = await fetch("/api/chat/technical", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                code: currentTechCode,
                question,
                latest_summary: currentTechSummary,
                conversation_id: techConversationId,
                beginner_mode: isBeginnerMode,
                pro_mode: isProMode,
            }),
        });
        const data = await res.json();
        loadingMsg.remove();

        if (!res.ok) {
            showGlobalErrors([data.error || "기술지표 설명 요청에 실패했습니다."]);
            appendChatMessage(techChatBox, "bot", `오류: ${data.error || "요청 실패"}`);
            console.error("[ERROR] 탭3 챗봇 오류", data.error);
            return;
        }

        techConversationId = data.conversation_id || techConversationId;
        appendChatMessage(techChatBox, "bot", data.answer || "(빈 응답)");
        console.log(`[TECH_CHAT] 탭3 지표 숫자 주입 및 Dify 챗봇③ 설명 수신 (code=${currentTechCode})`);
    } catch (err) {
        loadingMsg.remove();
        showGlobalErrors(["기술지표 설명 요청 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭3 챗봇 통신 오류", err);
    } finally {
        techSendBtn.disabled = false;
    }
});

resetTechChatBtn.addEventListener("click", () => {
    techConversationId = "";
    techChatBox.innerHTML = '<div class="chat-msg bot">새 대화가 시작되었습니다. 계산된 지표에 대해 질문해 보세요.</div>';
});

document.querySelectorAll(".tech-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        techCodeInput.value = btn.dataset.code;
    });
});

document.querySelectorAll(".tech-q-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        techQuestionInput.value = btn.dataset.q;
    });
});

copyTechBtn.addEventListener("click", () => {
    const code = currentTechCode || "tech";
    copyChatToClipboard(techChatBox, `기술지표 해설 (${code})`, copyTechBtn);
});

saveTechBtn.addEventListener("click", () => {
    const code = currentTechCode || "tech";
    downloadChatMarkdown(techChatBox, `기술지표 해설 (${code})`, `technical_${code}.md`);
});

// =========================================================
// 탭 4: 하루 1종목 추천
// =========================================================
const dailyPickBtn = document.getElementById("dailyPickBtn");
const dailyPickRefreshBtn = document.getElementById("dailyPickRefreshBtn");
const dailyPickLoading = document.getElementById("dailyPickLoading");
const dailyPickResult = document.getElementById("dailyPickResult");

const dpCodeBadge = document.getElementById("dpCodeBadge");
const dpDateBadge = document.getElementById("dpDateBadge");
const dpStockName = document.getElementById("dpStockName");
const dpCurrentPrice = document.getElementById("dpCurrentPrice");
const dpExpectedReturn = document.getElementById("dpExpectedReturn");
const dpPriceGrid = document.getElementById("dpPriceGrid");
const dpPriceRationale = document.getElementById("dpPriceRationale");
const dpCompanyIntro = document.getElementById("dpCompanyIntro");
const dpReasonsList = document.getElementById("dpReasonsList");
const dpRiskNote = document.getElementById("dpRiskNote");
const dpCandidatesTableBody = document.querySelector("#dpCandidatesTable tbody");

async function loadDailyPick(forceRefresh = false) {
    showGlobalErrors([]);
    dailyPickResult.classList.add("hidden");
    dailyPickLoading.classList.remove("hidden");
    dailyPickBtn.disabled = true;
    dailyPickRefreshBtn.disabled = true;

    try {
        const res = await fetch("/api/daily-pick", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                force_refresh: forceRefresh,
                beginner_mode: isBeginnerMode,
            }),
        });
        const data = await res.json();

        if (!res.ok) {
            showGlobalErrors([data.error || "오늘의 추천 종목을 불러오지 못했습니다."]);
            console.error("[ERROR] 탭4 추천 오류", data.error);
            return;
        }

        const p = data.pick || {};
        dpCodeBadge.textContent = p.selected_code || "-";
        dpDateBadge.textContent = `${data.date || "-"} 기준`;
        dpStockName.textContent = p.selected_name || "-";
        dpCurrentPrice.textContent = formatNumber(p.current_price, "원");

        const expRet = p.expected_return_pct;
        dpExpectedReturn.textContent = expRet != null ? `목표 기대수익률 ${expRet > 0 ? "+" : ""}${expRet}%` : "-";

        renderMetricGrid(dpPriceGrid, [
            { label: "현재가", value: formatNumber(p.current_price, "원") },
            { label: "추천 진입가격", value: formatNumber(p.entry_price, "원") },
            { label: "예상 판매가 (목표가)", value: formatNumber(p.target_price, "원") },
            { label: "손절 기준가", value: formatNumber(p.stop_loss_price, "원") },
        ]);

        dpPriceRationale.textContent = p.price_rationale || "";
        dpCompanyIntro.textContent = p.company_intro || "";

        dpReasonsList.innerHTML = "";
        (p.selection_reasons || []).forEach((reason) => {
            const li = document.createElement("li");
            li.className = "news-item";
            li.textContent = reason;
            dpReasonsList.appendChild(li);
        });

        dpRiskNote.textContent = p.risk_note || "-";

        dpCandidatesTableBody.innerHTML = "";
        const summaries = p.candidates_summary || [];
        if (summaries.length === 0) {
            (data.candidates_raw || []).forEach((c) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${escapeHtml(c.name)} (${escapeHtml(c.code)})</td>
                    <td>${formatNumber(c.current_price, "원")}</td>
                    <td>${formatNumber(c.per, "배")}</td>
                    <td>${formatNumber(c.rsi14)}</td>
                    <td>-</td>
                `;
                dpCandidatesTableBody.appendChild(tr);
            });
        } else {
            summaries.forEach((c) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${escapeHtml(c.name || "-")}</strong> (${escapeHtml(c.code || "-")})</td>
                    <td>${formatNumber(c.current_price, "원")}</td>
                    <td>${formatNumber(c.per, "배")}</td>
                    <td>${formatNumber(c.rsi14)}</td>
                    <td style="text-align: left; white-space: normal;">${escapeHtml(c.comment || "-")}</td>
                `;
                dpCandidatesTableBody.appendChild(tr);
            });
        }

        dailyPickResult.classList.remove("hidden");
        console.log(`[DAILY_PICK] 하루 1종목 추천 완료 (${p.selected_name}, model=${data.model})`);
    } catch (err) {
        showGlobalErrors(["추천 종목 요청 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭4 통신 오류", err);
    } finally {
        dailyPickLoading.classList.add("hidden");
        dailyPickBtn.disabled = false;
        dailyPickRefreshBtn.disabled = false;
    }
}

dailyPickBtn.addEventListener("click", () => loadDailyPick(false));
dailyPickRefreshBtn.addEventListener("click", () => loadDailyPick(true));

// =========================================================
// 초보자 / 전문가 모드 토글 & 초기화
// =========================================================
function applyBeginnerMode(enabled) {
    isBeginnerMode = enabled;
    localStorage.setItem("beginnerMode", enabled ? "true" : "false");
    document.body.classList.toggle("beginner-mode", enabled);
    beginnerModeBtn.classList.toggle("active", enabled);
    beginnerModeBtn.textContent = enabled ? "초보자 모드 ON" : "초보자 모드";
    if (enabled && isProMode) {
        applyProMode(false);
    }
}

function applyProMode(enabled) {
    isProMode = enabled;
    localStorage.setItem("proMode", enabled ? "true" : "false");
    document.body.classList.toggle("pro-mode", enabled);
    proModeBtn.classList.toggle("active", enabled);
    proModeBtn.textContent = enabled ? "전문가 모드 ON" : "전문가 모드";
    if (enabled && isBeginnerMode) {
        applyBeginnerMode(false);
    }
}

beginnerModeBtn.addEventListener("click", () => {
    applyBeginnerMode(!isBeginnerMode);
    console.log(`[PAGE] 초보자 모드 ${isBeginnerMode ? "켜짐" : "꺼짐"}`);
});

proModeBtn.addEventListener("click", () => {
    applyProMode(!isProMode);
    console.log(`[PAGE] 전문가 모드 ${isProMode ? "켜짐" : "꺼짐"}`);
});

document.addEventListener("DOMContentLoaded", () => {
    applyBeginnerMode(isBeginnerMode);
    applyProMode(isProMode);
    initTabs();
    checkServerHealth();
    console.log("[PAGE] 페이지 로드 및 탭 초기화 완료");
});
