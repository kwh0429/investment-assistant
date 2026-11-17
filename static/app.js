// =========================================================
// 상태 관리 변수
// =========================================================
let analysisConversationId = "";
let techConversationId = "";
let currentTechCode = "";
let currentTechSummary = null;

// Chart.js 인스턴스 (재조회 시 destroy 필수)
let weightChartInstance = null;
let frontierChartInstance = null;
let priceTechChartInstance = null;
let rsiChartInstance = null;

// =========================================================
// 공용 DOM 및 유틸리티
// =========================================================
const healthBadge = document.getElementById("healthBadge");
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

function appendChatMessage(container, role, text) {
    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

// =========================================================
// 서버 상태 확인 (/health) & 탭 전환
// =========================================================
async function checkServerHealth() {
    try {
        const res = await fetch("/health");
        const data = await res.json();
        const difyReady = data.dify_analysis_api_configured && data.dify_tech_api_configured;

        if (data.status === "ok" && data.serper_api_configured && difyReady) {
            healthBadge.textContent = "서버 정상 · Dify 2슬롯 준비됨";
            healthBadge.className = "health-badge ok";
        } else if (data.status === "ok") {
            const missing = [];
            if (!data.serper_api_configured) missing.push("Serper");
            if (!data.dify_analysis_api_configured) missing.push("Dify분석");
            if (!data.dify_tech_api_configured) missing.push("Dify기술");
            healthBadge.textContent = `서버 정상 · 미설정(${missing.join(", ")})`;
            healthBadge.className = "health-badge warn";
        }
    } catch (err) {
        healthBadge.textContent = "서버 연결 실패";
        healthBadge.className = "health-badge warn";
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
const analysisCodeInput = document.getElementById("analysisCode");
const useToolCheck = document.getElementById("useToolCheck");
const analysisChatBox = document.getElementById("analysisChatBox");
const analysisChatForm = document.getElementById("analysisChatForm");
const analysisQuestionInput = document.getElementById("analysisQuestion");
const analysisSendBtn = document.getElementById("analysisSendBtn");
const evidencePanel = document.getElementById("evidencePanel");
const resetAnalysisChatBtn = document.getElementById("resetAnalysisChatBtn");

function renderEvidencePanel(evidence, useTool) {
    evidencePanel.innerHTML = "";
    if (!useTool) {
        evidencePanel.innerHTML = '<p class="muted-text">⚠️ "도구 근거 사용"이 꺼져 있어 외부 데이터 없이 AI 단독 답변을 받았습니다.</p>';
        return;
    }
    if (!evidence) {
        evidencePanel.innerHTML = '<p class="muted-text">수집된 근거 데이터가 없습니다.</p>';
        return;
    }

    const snap = evidence.snapshot || {};
    const snapBlock = document.createElement("div");
    snapBlock.className = "evidence-block";
    snapBlock.innerHTML = `<h4>📌 ${evidence.name || "-"} (${evidence.code}) 시세·투자정보</h4>`;
    const snapText = document.createElement("p");
    snapText.textContent =
        `현재가: ${formatNumber(snap.current_price, "원")} (${snap.change_rate ?? "-"}%) | ` +
        `시총: ${formatKoreanAmount(snap.market_cap)} | ` +
        `PER: ${formatNumber(snap.per, "배")} (추정 ${formatNumber(snap.cns_per, "배")}) | ` +
        `PBR: ${formatNumber(snap.pbr, "배")}`;
    snapBlock.appendChild(snapText);
    evidencePanel.appendChild(snapBlock);

    const finBlock = document.createElement("div");
    finBlock.className = "evidence-block";
    finBlock.innerHTML = "<h4>📊 연간 실적 요약</h4>";
    (evidence.financials || []).forEach((f) => {
        const p = document.createElement("p");
        const tag = f.is_consensus ? " [예상(E)]" : " [확정]";
        p.textContent =
            `${f.period}${tag}: 매출 ${formatKoreanAmount(f.revenue)}, ` +
            `영업이익 ${formatKoreanAmount(f.operating_income)}, ROE ${formatNumber(f.roe, "%")}`;
        finBlock.appendChild(p);
    });
    evidencePanel.appendChild(finBlock);

    const newsBlock = document.createElement("div");
    newsBlock.className = "evidence-block";
    newsBlock.innerHTML = `<h4>📰 Serper 최신 뉴스 (${(evidence.news || []).length}건)</h4>`;
    (evidence.news || []).forEach((n) => {
        const p = document.createElement("p");
        p.textContent = `• ${n.title} (${n.source || "-"}, ${n.published || ""})`;
        newsBlock.appendChild(p);
    });
    evidencePanel.appendChild(newsBlock);
}

analysisChatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = analysisCodeInput.value.trim();
    const question = analysisQuestionInput.value.trim() || "이 종목의 현재 재무, 밸류에이션, 최신 뉴스를 종합해 객관적으로 분석해 주세요.";
    const useTool = useToolCheck.checked;

    showGlobalErrors([]);
    appendChatMessage(analysisChatBox, "user", `[${code || "종목미지정"} / ${useTool ? "도구ON" : "도구OFF"}] ${question}`);
    analysisQuestionInput.value = "";

    const loadingMsg = appendChatMessage(
        analysisChatBox,
        "bot loading",
        useTool ? "네이버 금융 + Serper 뉴스 수집 후 Dify 챗봇① 응답 생성 중..." : "Dify 챗봇① 응답 생성 중..."
    );
    analysisSendBtn.disabled = true;

    try {
        const res = await fetch("/api/chat/analysis", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                code,
                question,
                use_tool: useTool,
                conversation_id: analysisConversationId,
            }),
        });
        const data = await res.json();
        loadingMsg.remove();

        if (!res.ok) {
            showGlobalErrors([data.error || "심층분석 요청에 실패했습니다."]);
            appendChatMessage(analysisChatBox, "bot", `⚠️ 오류: ${data.error || "요청 실패"}`);
            renderEvidencePanel(data.evidence, useTool);
            console.error("[ERROR] 탭1 심층분석 오류", data.error);
            return;
        }

        analysisConversationId = data.conversation_id || analysisConversationId;
        appendChatMessage(analysisChatBox, "bot", data.answer || "(빈 응답)");
        renderEvidencePanel(data.evidence, data.use_tool);

        if (data.collect_errors && data.collect_errors.length > 0) {
            showGlobalErrors(data.collect_errors);
        }
        console.log(`[ANALYSIS] 탭1 심층분석 요청 및 Dify 챗봇① 응답 수신 (code=${code}, use_tool=${useTool})`);
    } catch (err) {
        loadingMsg.remove();
        showGlobalErrors(["서버 통신 중 네트워크 오류가 발생했습니다."]);
        console.error("[ERROR] 탭1 통신 오류", err);
    } finally {
        analysisSendBtn.disabled = false;
    }
});

resetAnalysisChatBtn.addEventListener("click", () => {
    analysisConversationId = "";
    analysisChatBox.innerHTML = '<div class="chat-msg bot">새 대화가 시작되었습니다. 종목코드와 질문을 입력해 주세요.</div>';
    evidencePanel.innerHTML = '<p class="muted-text">질문을 전송하면 수집된 근거 데이터가 여기에 표시됩니다.</p>';
});

document.querySelectorAll(".analysis-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
        analysisCodeInput.value = btn.dataset.code;
    });
});

// =========================================================
// 탭 2: 리밸런싱 도우미 (Dify 없음 · 순수 Python 계산)
// =========================================================
const rebalanceForm = document.getElementById("rebalanceForm");
const rebalanceTickersInput = document.getElementById("rebalanceTickers");
const rebalanceBtn = document.getElementById("rebalanceBtn");
const rebalanceLoading = document.getElementById("rebalanceLoading");
const rebalanceResult = document.getElementById("rebalanceResult");
const rebalanceSummaryGrid = document.getElementById("rebalanceSummaryGrid");
const assetStatsTableBody = document.querySelector("#assetStatsTable tbody");

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

        assetStatsTableBody.innerHTML = "";
        data.tickers.forEach((code) => {
            const stat = data.asset_stats[code] || {};
            const weightPct = ((opt.weights[code] || 0) * 100).toFixed(2);
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${code}</td>
                <td><strong>${weightPct}%</strong></td>
                <td>${stat.annual_return ?? "-"}%</td>
                <td>${stat.volatility ?? "-"}%</td>
                <td>${stat.sharpe_ratio ?? "-"}</td>
                <td class="neg-val">${stat.mdd ?? "-"}%</td>
            `;
            assetStatsTableBody.appendChild(tr);
        });

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

const techChatBox = document.getElementById("techChatBox");
const techChatForm = document.getElementById("techChatForm");
const techQuestionInput = document.getElementById("techQuestion");
const techSendBtn = document.getElementById("techSendBtn");
const resetTechChatBtn = document.getElementById("resetTechChatBtn");

function renderTechCharts(data) {
    const ind = data.indicators;

    if (priceTechChartInstance) priceTechChartInstance.destroy();
    const pCtx = document.getElementById("priceTechChart").getContext("2d");
    priceTechChartInstance = new Chart(pCtx, {
        type: "line",
        data: {
            labels: data.dates,
            datasets: [
                { label: "종가", data: data.close, borderColor: "#1e293b", borderWidth: 2, pointRadius: 0 },
                { label: "MA5", data: ind.ma5, borderColor: "#ef4444", borderWidth: 1.5, pointRadius: 0 },
                { label: "MA20", data: ind.ma20, borderColor: "#f59e0b", borderWidth: 1.5, pointRadius: 0 },
                { label: "MA60", data: ind.ma60, borderColor: "#10b981", borderWidth: 1.5, pointRadius: 0 },
                { label: "볼린저 상단", data: ind.bb_upper, borderColor: "#94a3b8", borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
                { label: "볼린저 하단", data: ind.bb_lower, borderColor: "#94a3b8", borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
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

    const loadingMsg = appendChatMessage(techChatBox, "bot loading", "계산된 지표를 주입해 Dify 챗봇③이 설명 생성 중...");
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
            }),
        });
        const data = await res.json();
        loadingMsg.remove();

        if (!res.ok) {
            showGlobalErrors([data.error || "기술지표 설명 요청에 실패했습니다."]);
            appendChatMessage(techChatBox, "bot", `⚠️ 오류: ${data.error || "요청 실패"}`);
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

// =========================================================
// 초기화
// =========================================================
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    checkServerHealth();
    console.log("[PAGE] 페이지 로드 및 탭 초기화 완료");
});
