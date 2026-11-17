// 현재 화면에 수집된 종목 데이터 (AI 브리핑 요청 시 재사용)
let currentStockData = null;

// DOM 요소 참조
const healthBadge = document.getElementById("healthBadge");
const searchForm = document.getElementById("searchForm");
const stockCodeInput = document.getElementById("stockCodeInput");
const searchBtn = document.getElementById("searchBtn");
const loadingBox = document.getElementById("loadingBox");
const loadingText = document.getElementById("loadingText");
const errorBox = document.getElementById("errorBox");
const errorList = document.getElementById("errorList");
const dashboard = document.getElementById("dashboard");

// 스냅샷 요소
const stockCodeBadge = document.getElementById("stockCodeBadge");
const stockNameEl = document.getElementById("stockName");
const currentPriceEl = document.getElementById("currentPrice");
const changeRateEl = document.getElementById("changeRate");
const quoteGrid = document.getElementById("quoteGrid");
const investGrid = document.getElementById("investGrid");

// AI 브리핑 요소
const briefBtn = document.getElementById("briefBtn");
const briefLoading = document.getElementById("briefLoading");
const briefContent = document.getElementById("briefContent");

// 실적 테이블 & 뉴스 요소
const financialsTable = document.getElementById("financialsTable");
const newsList = document.getElementById("newsList");

// 숫자 포맷 유틸
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

// 오류 배너 표시/숨김
function showErrors(errors) {
    if (!errors || errors.length === 0) {
        errorBox.classList.add("hidden");
        errorList.innerHTML = "";
        return;
    }
    errorList.innerHTML = "";
    errors.forEach((msg) => {
        const li = document.createElement("li");
        li.textContent = msg;
        errorList.appendChild(li);
    });
    errorBox.classList.remove("hidden");
}

// 서버 상태 확인 (/health)
async function checkServerHealth() {
    try {
        const res = await fetch("/health");
        const data = await res.json();
        if (data.status === "ok" && data.serper_api_configured && data.gemini_api_configured) {
            healthBadge.textContent = `서버 정상 · ${data.gemini_model}`;
            healthBadge.className = "health-badge ok";
        } else {
            healthBadge.textContent = "서버 정상 · 일부 API Key 미설정";
            healthBadge.className = "health-badge warn";
        }
    } catch (err) {
        healthBadge.textContent = "서버 연결 실패";
        healthBadge.className = "health-badge warn";
        console.error("[ERROR] 서버 상태 확인 실패", err);
    }
}

// 1·2구역 지표 카드 렌더링 helper
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

// 3구역 기업실적분석 테이블 렌더링
function renderFinancialsTable(financials) {
    const thead = financialsTable.querySelector("thead");
    const tbody = financialsTable.querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    if (!financials || financials.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5">수집된 실적 데이터가 없습니다.</td></tr>';
        return;
    }

    // 헤더 행 (기간 + 예상(E) 뱃지)
    const headerRow = document.createElement("tr");
    const metricTh = document.createElement("th");
    metricTh.textContent = "주요 재무지표";
    headerRow.appendChild(metricTh);

    financials.forEach((f) => {
        const th = document.createElement("th");
        if (f.is_consensus) {
            th.className = "consensus-col";
            th.innerHTML = `${f.period} <span class="consensus-badge">예상(E)</span>`;
        } else {
            th.textContent = f.period;
        }
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    // 표시할 지표 행 목록
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
        const labelTd = document.createElement("td");
        labelTd.textContent = label;
        tr.appendChild(labelTd);

        financials.forEach((f) => {
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

// 뉴스 목록 렌더링
function renderNewsList(news) {
    newsList.innerHTML = "";
    if (!news || news.length === 0) {
        const li = document.createElement("li");
        li.className = "news-item";
        li.textContent = "수집된 최신 뉴스가 없습니다.";
        newsList.appendChild(li);
        return;
    }

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

        newsList.appendChild(li);
    });
}

// 전체 데이터 대시보드 렌더링
function renderDashboard(data) {
    const snap = data.snapshot || {};

    stockCodeBadge.textContent = data.code;
    stockNameEl.textContent = data.name || "종목명 미확인";
    currentPriceEl.textContent = formatNumber(snap.current_price, "원");

    const rate = snap.change_rate;
    if (rate !== null && rate !== undefined) {
        const sign = rate > 0 ? "+" : "";
        changeRateEl.textContent = `전일대비 ${sign}${rate}%`;
        changeRateEl.className = "change-rate " + (rate > 0 ? "up" : rate < 0 ? "down" : "flat");
    } else {
        changeRateEl.textContent = "-";
        changeRateEl.className = "change-rate flat";
    }

    // 1구역: 당일 시세
    renderMetricGrid(quoteGrid, [
        { label: "전일 종가", value: formatNumber(snap.prev_close, "원") },
        { label: "시가", value: formatNumber(snap.open_price, "원") },
        { label: "고가", value: formatNumber(snap.high_price, "원") },
        { label: "저가", value: formatNumber(snap.low_price, "원") },
        { label: "거래량", value: formatNumber(snap.volume, "주") },
        { label: "거래대금", value: formatKoreanAmount(snap.trading_value) },
    ]);

    // 2구역: 투자정보
    renderMetricGrid(investGrid, [
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

    // 3구역: 기업실적분석
    renderFinancialsTable(data.financials);

    // 4구역: 뉴스 리스트
    renderNewsList(data.news);

    // 이전 AI 브리핑 초기화
    briefContent.classList.add("hidden");
    briefContent.textContent = "";
    briefBtn.disabled = false;

    dashboard.classList.remove("hidden");
    console.log("[RENDER] 화면 데이터 카드 렌더링 완료");
}

// 종목 데이터 수집 요청 (POST /fetch)
async function fetchStockData(code) {
    const cleanCode = code.trim();
    console.log(`[SEARCH] 종목 수집 요청 시작 (${cleanCode})`);

    showErrors([]);
    dashboard.classList.add("hidden");
    loadingText.textContent = `'${cleanCode}' 종목 데이터를 네이버 금융과 Serper에서 수집 중입니다...`;
    loadingBox.classList.remove("hidden");
    searchBtn.disabled = true;

    try {
        const res = await fetch("/fetch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: cleanCode }),
        });

        const data = await res.json();

        if (!res.ok) {
            showErrors(data.errors || ["데이터 수집에 실패했습니다."]);
            console.error("[ERROR] 수집 중 오류 발생", data.errors);
            return;
        }

        currentStockData = data;

        const priceText = data.snapshot?.current_price ?? "-";
        console.log(`[PARSED] 기본 정보 수집 완료 (${data.name}, 현재가=${priceText})`);
        console.log(`[NEWS] 뉴스 수집 완료 (${(data.news || []).length}건)`);

        if (data.errors && data.errors.length > 0) {
            showErrors(data.errors);
            console.warn("[ERROR] 일부 항목 수집 중 오류 발생", data.errors);
        }

        renderDashboard(data);
    } catch (err) {
        showErrors(["서버와 통신하는 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 수집 중 오류 발생", err);
    } finally {
        loadingBox.classList.add("hidden");
        searchBtn.disabled = false;
    }
}

// Gemini AI 브리핑 요청 (POST /brief)
async function generateAiBriefing() {
    if (!currentStockData) return;

    briefBtn.disabled = true;
    briefLoading.classList.remove("hidden");
    briefContent.classList.add("hidden");

    try {
        const res = await fetch("/brief", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(currentStockData),
        });

        const data = await res.json();

        if (!res.ok) {
            showErrors([data.error || "AI 브리핑 생성에 실패했습니다."]);
            console.error("[ERROR] 수집 중 오류 발생 (AI 브리핑)", data.error);
            briefBtn.disabled = false;
            return;
        }

        briefContent.textContent = data.briefing;
        briefContent.classList.remove("hidden");
        console.log(`[BRIEF] Gemini AI 브리핑 생성 완료 (model=${data.model})`);
    } catch (err) {
        showErrors(["AI 브리핑 요청 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 수집 중 오류 발생 (AI 브리핑)", err);
        briefBtn.disabled = false;
    } finally {
        briefLoading.classList.add("hidden");
    }
}

// 이벤트 바인딩
document.addEventListener("DOMContentLoaded", () => {
    console.log("[PAGE] 페이지 로드 완료");
    checkServerHealth();
});

searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    fetchStockData(stockCodeInput.value);
});

document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        const code = btn.dataset.code;
        stockCodeInput.value = code;
        fetchStockData(code);
    });
});

briefBtn.addEventListener("click", generateAiBriefing);
