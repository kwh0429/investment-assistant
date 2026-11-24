import os
import re
import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from google import genai
from collector import collect_stock_data, fetch_daily_candidates
from quant import calculate_rebalancing, calculate_technical_indicators
from dify_client import call_dify_chatbot, call_gemini_with_fallback

# 당일 하루 1종목 추천 캐시
_DAILY_PICK_CACHE = {"date": None, "data": None}

# 환경변수(.env) 로드
load_dotenv()

# 로깅 설정 (print 대신 표준 logging 사용)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    """메인 웹페이지 (index.html 렌더링)"""
    return render_template('index.html')


@app.route('/test', methods=['GET'])
def test_page():
    """기존 1차 종목 데이터 수집·브리핑 테스트 화면 (test.html 렌더링)"""
    return render_template('test.html')


@app.route('/health', methods=['GET'])
def health():
    """
    서버 상태 및 필수 API Key 설정 여부만 확인하여 반환
    (보안 절대 규칙: 실제 키 값은 절대 외부로 노출하지 않음)
    """
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    dify_analysis_key = os.getenv("DIFY_ANALYSIS_API_KEY", "").strip()
    dify_tech_key = os.getenv("DIFY_TECH_API_KEY", "").strip()
    dart_key = os.getenv("DART_API_KEY", "").strip()

    is_serper_ready = bool(serper_key)
    is_gemini_ready = bool(gemini_key)
    is_dify_analysis_ready = bool(dify_analysis_key)
    is_dify_tech_ready = bool(dify_tech_key)
    is_dart_ready = bool(dart_key)

    logger.info(
        f"[HEALTH] 상태 점검 요청 - Serper: {is_serper_ready}, Gemini: {is_gemini_ready}, "
        f"Dify분석: {is_dify_analysis_ready}, Dify기술: {is_dify_tech_ready}, DART: {is_dart_ready}"
    )

    return jsonify({
        "status": "ok",
        "serper_api_configured": is_serper_ready,
        "gemini_api_configured": is_gemini_ready,
        "gemini_model": gemini_model,
        "dify_analysis_api_configured": is_dify_analysis_ready,
        "dify_tech_api_configured": is_dify_tech_ready,
        "dart_api_configured": is_dart_ready,
    }), 200


@app.route('/fetch', methods=['POST'])
def fetch():
    """
    종목코드를 받아 네이버 금융 데이터 수집 + Serper 뉴스 검색을 통합 실행하고 JSON 반환
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            logger.warning("[REQUEST] POST /fetch 잘못된 요청 형식 (JSON 아님)")
            return jsonify({
                "code": "",
                "name": None,
                "snapshot": {},
                "financials": [],
                "news": [],
                "errors": ["요청 형식이 올바르지 않습니다. JSON 형태로 종목코드를 보내주세요."]
            }), 400

        code = str(payload.get("code", "")).strip()
        logger.info(f"[REQUEST] POST /fetch code={code}")

        result = collect_stock_data(code)
        err_count = len(result.get("errors", []))
        news_count = len(result.get("news", []))
        fin_count = len(result.get("financials", []))

        logger.info(
            f"[RESPONSE] 수집 완료 (code={result.get('code')}, "
            f"name={result.get('name')}, financials={fin_count}, news={news_count}, errors={err_count})"
        )

        # 종목코드 자체가 잘못되어 아무것도 수집되지 않은 입력 오류는 400 반환
        if not result.get("name") and not result.get("snapshot") and err_count > 0:
            return jsonify(result), 400

        return jsonify(result), 200

    except Exception:
        logger.error("[ERROR] POST /fetch 처리 중 예기치 않은 오류 발생", exc_info=True)
        return jsonify({
            "code": "",
            "name": None,
            "snapshot": {},
            "financials": [],
            "news": [],
            "errors": ["서버 내부 오류가 발생했습니다. 백엔드 로그를 확인해 주세요."]
        }), 500


@app.route('/brief', methods=['POST'])
def brief():
    """
    수집된 종목 JSON 데이터를 Gemini 3.5 Flash Lite 모델에 전달하여
    초보자용 객관적 투자 브리핑 리포트 생성 및 반환
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API Key가 설정되지 않았습니다 (.env 확인 필요)."}), 400

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()

    stock_data = request.get_json(silent=True)
    if not isinstance(stock_data, dict) or not stock_data.get("name"):
        return jsonify({"error": "브리핑을 생성할 종목 데이터가 올바르지 않습니다."}), 400

    stock_name = stock_data.get("name")
    stock_code = stock_data.get("code", "")
    logger.info(f"[GEMINI] Gemini 호출 시작 (model={gemini_model}, target={stock_name})")

    prompt = f"""당신은 초보 투자자를 돕는 친절하고 객관적인 주식 데이터 브리핑 어시스턴트입니다.
아래 제공된 JSON 데이터를 바탕으로 '{stock_name}({stock_code})' 종목의 현재 상태를 알기 쉽게 분석해 주세요.

[절대 규칙]
1. 매수/매도 추천(사라, 팔아라)이나 목표주가 단정은 절대 하지 마세요. 오직 데이터 기반의 객관적인 해석만 제공합니다.
2. 'is_consensus: true'로 표시된 실적은 아직 확정되지 않은 증권사 컨센서스(예상치)이므로, 반드시 '예상치(전망)'임을 명확히 구분해 서술하세요.
3. 제공된 JSON에 없는 숫자를 임의로 지어내지 마세요. (금액은 원 단위이므로 조/억 단위로 보기 좋게 환산해 설명하세요.)
4. 초보자가 이해하기 쉽게 아래 5가지 항목으로 구조화하여 마크다운 형식으로 작성하세요.

[브리핑 구성]
1. 기업 체급 및 현재 위치 (시가총액, 52주 최고/최저 대비 현재 주가 위치, 외인소진율)
2. 실적 성장성과 수익성 (매출액·영업이익 추이, 영업이익률, ROE)
3. 재무 건전성 (부채비율 점검)
4. 밸류에이션 평가 (현재 PER vs 추정 PER, PBR 비교)
5. 최신 뉴스 핵심 요약 및 종합 관전 포인트

[수집 데이터 JSON]
{json.dumps(stock_data, ensure_ascii=False, indent=2)}
"""

    fb = call_gemini_with_fallback(prompt)
    if fb.get("error"):
        return jsonify({"error": fb["error"]}), 500

    logger.info(f"[RESPONSE] AI 브리핑 생성 완료 (target={stock_name}, model={fb['model']}, length={len(fb['answer'])})")
    return jsonify({
        "model": fb["model"],
        "briefing": fb["answer"],
    }), 200


@app.route('/api/rebalance', methods=['POST'])
def api_rebalance():
    """
    [탭 2] 보유 종목/ETF 코드 리스트를 받아 최근 1년 FDR 주가 기반
    단순화 몬테카를로 + 효율적 프론티어 비중 계산 JSON 반환 (Dify 미사용)
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"errors": ["요청 형식이 올바르지 않습니다 (JSON 필요)."]}), 400

        raw_tickers = payload.get("tickers", [])
        if isinstance(raw_tickers, str):
            tickers = [t.strip() for t in raw_tickers.split(",") if t.strip()]
        elif isinstance(raw_tickers, list):
            tickers = [str(t).strip() for t in raw_tickers if str(t).strip()]
        else:
            tickers = []

        logger.info(f"[REQUEST] POST /api/rebalance tickers={tickers}")
        result = calculate_rebalancing(tickers)

        err_count = len(result.get("errors", []))
        logger.info(
            f"[RESPONSE] 리밸런싱 완료 (valid={len(result.get('tickers', []))}, errors={err_count})"
        )

        if result.get("optimal_portfolio") is None:
            return jsonify(result), 400
        return jsonify(result), 200

    except Exception:
        logger.error("[ERROR] POST /api/rebalance 처리 중 오류 발생", exc_info=True)
        return jsonify({"errors": ["리밸런싱 계산 중 서버 내부 오류가 발생했습니다."]}), 500


@app.route('/api/technical', methods=['POST'])
def api_technical():
    """
    [탭 3] 종목코드를 받아 FinanceDataReader 주가 데이터로
    MA(5/20/60), RSI(14), MACD, 볼린저밴드를 계산하여 차트용 JSON 반환
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"errors": ["요청 형식이 올바르지 않습니다 (JSON 필요)."]}), 400

        code = str(payload.get("code", "")).strip()
        logger.info(f"[REQUEST] POST /api/technical code={code}")

        if not code:
            return jsonify({"errors": ["종목코드를 입력해 주세요."]}), 400

        result = calculate_technical_indicators(code)
        err_count = len(result.get("errors", []))
        logger.info(
            f"[RESPONSE] 기술지표 완료 (code={code}, days={len(result.get('dates', []))}, errors={err_count})"
        )

        if err_count > 0:
            return jsonify(result), 400
        return jsonify(result), 200

    except Exception:
        logger.error("[ERROR] POST /api/technical 처리 중 오류 발생", exc_info=True)
        return jsonify({"errors": ["기술지표 계산 중 서버 내부 오류가 발생했습니다."]}), 500


@app.route('/api/chat/analysis', methods=['POST'])
def api_chat_analysis():
    """
    [탭 1] 종목 심층분석 채팅 (Dify 챗봇① 연결)
    - action="init": 종목코드 수집(collector.py) + 전문가 애널리스트 프롬프트로 1차 종합 분석 생성
    - action="chat": 열린 conversation_id를 유지하며 후속 질문 이어가기
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "요청 형식이 올바르지 않습니다 (JSON 필요)."}), 400

        action = str(payload.get("action", "init")).strip()
        code = str(payload.get("code", "")).strip()
        question = str(payload.get("question", "")).strip()
        use_tool = bool(payload.get("use_tool", True))
        beginner_mode = bool(payload.get("beginner_mode", False))
        pro_mode = bool(payload.get("pro_mode", False))
        conversation_id = str(payload.get("conversation_id") or "").strip()

        # 내 평단가 / 수량 (선택 입력)
        raw_avg = payload.get("avg_price")
        raw_qty = payload.get("quantity")
        avg_price = float(raw_avg) if raw_avg not in (None, "", 0, "0") else None
        quantity = int(float(raw_qty)) if raw_qty not in (None, "", 0, "0") else None

        logger.info(f"[REQUEST] POST /api/chat/analysis action={action}, code={code}, use_tool={use_tool}")

        evidence = None
        collect_errors = []
        user_position = None

        def _build_position_text(curr_price):
            nonlocal user_position
            if not avg_price or avg_price <= 0:
                return ""
            if curr_price and curr_price > 0:
                ret_pct = round((curr_price - avg_price) / avg_price * 100, 2)
                pnl = round((curr_price - avg_price) * quantity) if quantity else None
                user_position = {
                    "avg_price": avg_price,
                    "quantity": quantity,
                    "current_price": curr_price,
                    "return_pct": ret_pct,
                    "pnl": pnl,
                }
                qty_str = f", 보유수량 {quantity}주, 평가손익 {pnl:,}원" if quantity else ""
                return f"\n[고객 실제 보유 현황]\n- 평균 매수가(평단가): {avg_price:,.0f}원\n- 현재가: {curr_price:,.0f}원\n- 현재 손익률: {ret_pct:+.2f}%{qty_str}\n반드시 고객의 이 평단가와 손익률을 기준으로 맞춤형 PB 전략(물타기/보유/비중축소/손절 기준선)을 제시하세요.\n"
            return ""

        def _compact_evidence(ev):
            """Dify 토큰 절약 및 빠른 응답을 위해 핵심 필드 위주로 압축"""
            return {
                "code": ev.get("code"),
                "name": ev.get("name"),
                "snapshot": ev.get("snapshot"),
                "market_indices": ev.get("market_indices"),
                "consensus": ev.get("consensus"),
                "investor_trends": (ev.get("investor_trends") or [])[:5],
                "peers": (ev.get("peers") or [])[:3],
                "researches": (ev.get("researches") or [])[:3],
                "financials": ev.get("financials"),
                "quarter_financials": (ev.get("quarter_financials") or [])[-4:],
                "ezinit_analysis": ev.get("ezinit_analysis"),
                "dart_disclosures": ev.get("dart_disclosures"),
                "news": [{"title": n.get("title"), "source": n.get("source"), "published": n.get("published")} for n in (ev.get("news") or [])],
            }

        if action == "init":
            if not code:
                return jsonify({"error": "종목코드 또는 종목명을 입력해 주세요."}), 400

            if use_tool:
                # 프론트엔드가 이미 /fetch로 수집한 evidence를 넘겨주었으면 재수집 생략 (속도/타임아웃 최적화)
                provided_ev = payload.get("evidence")
                if isinstance(provided_ev, dict) and provided_ev.get("name"):
                    evidence = provided_ev
                    collect_errors = evidence.get("errors", [])
                else:
                    evidence = collect_stock_data(code)
                    collect_errors = evidence.get("errors", [])
                    if not evidence.get("name") and not evidence.get("snapshot") and collect_errors:
                        return jsonify({"error": collect_errors[0], "evidence": evidence}), 400

                stock_name = evidence.get("name") or code
                resolved_code = evidence.get("code") or code
                curr_price = (evidence.get("snapshot") or {}).get("current_price")
                pos_text = _build_position_text(curr_price)
                compact_json = json.dumps(_compact_evidence(evidence), ensure_ascii=False)
                beginner_guide = (
                    "\n[초보자 눈높이 설명 모드 ON]\n"
                    "어려운 금융 용어(PER, PBR, ROE, 컨센서스, 수급 등)를 사용할 때마다 괄호 안에 일상적인 쉬운 비유(예: '본전 뽑는 데 걸리는 햇수')를 붙여 아주 친절하게 설명해 주세요.\n"
                    if beginner_mode else ""
                )
                pro_guide = (
                    "\n[기관/전문가 애널리스트 모드 ON]\n"
                    "기초 용어 풀이는 생략하고, Fwd PER 멀티플 디레이팅/리레이팅, QoQ/YoY 실적 모멘텀, 목표주가 괴리율(Upside), 52주 고점 대비 낙폭(MDD), 외인/기관 수급 강도 등 기관 리서치 수준의 전문 용어와 정량적 인사이트로 밀도 있게 분석해 주세요.\n"
                    if pro_mode else ""
                )

                full_query = (
                    f"당신은 사용자 전담 개인 PB(Private Banker)입니다. 아래 제공된 '{stock_name}({resolved_code})'의 "
                    "실제 수집 데이터(JSON)만을 근거로, 고객을 위한 명확하고 실전적인 심층분석 리포트와 투자 전략을 작성해 주세요.\n"
                    f"{pos_text}{beginner_guide}{pro_guide}\n"
                    "[개인 PB 작성 원칙]\n"
                    "1. '투자는 본인 책임', '상황에 따라 다릅니다' 같은 원론적이고 회피성 멘트는 절대 쓰지 마세요.\n"
                    "2. 데이터에 기반해 현재 포지션 의견(예: 비중 확대 / 보유 유지 / 분할 익절 / 관망 / 손절 주의)을 명확히 제시하세요.\n"
                    "3. 시장 지수(KOSPI/KOSDAQ) 대비 상대강도, 최근 5일 외국인/기관 수급, 증권사 목표주가, 우쎔AI 리포트/DART 공시까지 모두 종합해 판단하세요.\n"
                    "4. 연간/분기 실적에서 `is_consensus: true`인 기간은 확정 실적이 아닌 '증권사 예상치(E)'임을 반드시 구분해 서술하세요.\n"
                    "5. 금액은 조/억 단위로 환산하고, 표·글머리 기호 등 깔끔한 마크다운으로 정리하세요.\n\n"
                    "[필수 분석 목차]\n"
                    "### 1. PB 종합 의견 (현재 스탠스 및 핵심 이유 1줄 요약)\n"
                    "### 2. 시장 대비 수급 및 체급 (KOSPI 대비 흐름, 외인/기관 5일 수급, 시가총액)\n"
                    "### 3. 실적 성장성과 수익성 (연간·분기 실적 추이, 영업이익률, ROE)\n"
                    "### 4. 밸류에이션 및 목표주가 괴리율 (현재 PER vs 추정 PER, PBR, 증권사 목표주가)\n"
                    "### 5. 최신 뉴스·DART 공시 이슈 및 구체적 대응 전략 (지지선·목표가 라인)\n\n"
                    f"[수집된 근거 데이터 JSON]\n{compact_json}"
                )
            else:
                full_query = (
                    f"당신은 개인 전담 PB입니다. '{code}' 종목에 대해 "
                    "명확한 투자 스탠스, 실적 흐름, 밸류에이션, 대응 전략을 5가지 목차로 단호하게 분석해 주세요."
                )

            # 새 분석 세션 시작 (conversation_id 초기화)
            dify_res = call_dify_chatbot("analysis", full_query, conversation_id="")

        else:
            # 후속 채팅 (기존 conversation_id 유지 + 개인 PB 판단 지침 적용)
            if not question:
                return jsonify({"error": "질문 내용을 입력해 주세요."}), 400

            pb_directive = (
                "\n\n[개인 PB 응답 원칙]\n"
                "당신은 이 사용자의 개인 전담 PB(테스트 전용)입니다. "
                "'최종 결정은 본인이 내리세요', '장단점이 모두 있습니다' 같은 애매한 양비론으로 회피하지 마세요. "
                "수집된 종목 데이터와 사용자가 말한 평단가/상황을 직접 계산해, "
                "첫 문장에서 명확한 행동 결론(예: 보유 유지, 분할 매수, 반등 시 비중 축소, 손절 등)을 단호하게 내린 뒤, "
                "구체적인 수치 근거와 기준 가격(목표가/손절가 라인)을 설명하세요."
                + ("\n(초보자 모드 ON: 전문 용어마다 쉬운 비유와 한 줄 풀이를 덧붙여 주세요.)" if beginner_mode else "")
                + ("\n(전문가 모드 ON: 기초 설명 없이 멀티플 변화, QoQ/YoY 실적, 수급 강도, 괴리율 등 기관급 정량 분석으로 답변하세요.)" if pro_mode else "")
            )

            if not conversation_id and use_tool and code:
                evidence = collect_stock_data(code)
                collect_errors = evidence.get("errors", [])
                curr_price = (evidence.get("snapshot") or {}).get("current_price")
                pos_text = _build_position_text(curr_price)
                compact_json = json.dumps(_compact_evidence(evidence), ensure_ascii=False)
                full_query = (
                    f"[근거 데이터 JSON]\n{compact_json}\n"
                    f"{pos_text}\n"
                    f"[사용자 질문]\n{question}"
                    f"{pb_directive}"
                )
            else:
                full_query = f"{question}{pb_directive}"

            dify_res = call_dify_chatbot("analysis", full_query, conversation_id=conversation_id)

        if dify_res.get("error"):
            return jsonify({
                "error": dify_res["error"],
                "evidence": evidence,
                "user_position": user_position,
                "collect_errors": collect_errors,
            }), 502 if "API Key" not in dify_res["error"] else 400

        logger.info(f"[RESPONSE] 탭1 심층분석 완료 (action={action}, code={code}, use_tool={use_tool})")
        return jsonify({
            "answer": dify_res["answer"],
            "conversation_id": dify_res["conversation_id"],
            "use_tool": use_tool,
            "evidence": evidence,
            "user_position": user_position,
            "collect_errors": collect_errors,
        }), 200

    except Exception:
        logger.error("[ERROR] POST /api/chat/analysis 처리 중 오류 발생", exc_info=True)
        return jsonify({"error": "심층분석 채팅 처리 중 서버 내부 오류가 발생했습니다."}), 500


@app.route('/api/chat/technical', methods=['POST'])
def api_chat_technical():
    """
    [탭 3] 기술지표 설명 전용 채팅 (Dify 챗봇③ 연결)
    - 웹앱이 계산한 최신 기술지표 숫자(latest_summary)를 보이지 않게 주입하여 설명 요청
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "요청 형식이 올바르지 않습니다 (JSON 필요)."}), 400

        code = str(payload.get("code", "")).strip()
        question = str(payload.get("question", "")).strip() or "현재 계산된 이동평균선, RSI, MACD, 볼린저밴드 수치를 초보자가 이해하기 쉽게 설명해 주세요."
        beginner_mode = bool(payload.get("beginner_mode", False))
        pro_mode = bool(payload.get("pro_mode", False))
        conversation_id = str(payload.get("conversation_id") or "").strip()
        summary = payload.get("latest_summary")

        logger.info(f"[REQUEST] POST /api/chat/technical code={code}")

        if not code:
            return jsonify({"error": "종목코드를 먼저 조회해 주세요."}), 400

        # 클라이언트가 요약 숫자를 안 보냈다면 백엔드에서 직접 계산
        if not isinstance(summary, dict) or not summary:
            tech_res = calculate_technical_indicators(code)
            if tech_res.get("errors"):
                return jsonify({"error": tech_res["errors"][0]}), 400
            summary = tech_res.get("latest_summary", {})

        beginner_tech_note = (
            " 특히 초보자 모드가 켜져 있으므로 RSI는 '시장의 체온계', 이동평균선은 '평균 기운', "
            "볼린저밴드는 '주가의 울타리'처럼 일상적인 비유를 사용해 아주 쉽게 설명해 주세요."
            if beginner_mode else ""
        )
        pro_tech_note = (
            " 특히 전문가 모드가 켜져 있으므로 20일 이격도(disparity_20), 볼린저밴드 폭(bb_width) 스퀴즈 여부, "
            "%B 위치(bb_pct_b), MACD 히스토그램(macd_hist)의 모멘텀 가속/감속을 중심으로 전문 트레이더 관점에서 정량 분석해 주세요."
            if pro_mode else ""
        )

        full_query = (
            "[계산된 최신 기술지표 숫자 (FinanceDataReader 기반)]\n"
            f"종목코드: {code}\n"
            f"{json.dumps(summary, ensure_ascii=False)}\n\n"
            "[지침]\n"
            "오직 위에 제공된 숫자만을 근거로 현재 이동평균선 배열, RSI 수준, MACD 추세, 볼린저밴드 위치를 "
            f"설명하세요. 매수/매도 추천은 하지 마세요.{beginner_tech_note}{pro_tech_note}\n\n"
            f"[사용자 질문]\n{question}"
        )

        dify_res = call_dify_chatbot("tech", full_query, conversation_id=conversation_id)
        if dify_res.get("error"):
            return jsonify({
                "error": dify_res["error"],
                "injected_summary": summary,
            }), 502 if "API Key" not in dify_res["error"] else 400

        logger.info(f"[RESPONSE] 탭3 기술지표 채팅 완료 (code={code})")
        return jsonify({
            "answer": dify_res["answer"],
            "conversation_id": dify_res["conversation_id"],
            "injected_summary": summary,
        }), 200

    except Exception:
        logger.error("[ERROR] POST /api/chat/technical 처리 중 오류 발생", exc_info=True)
        return jsonify({"error": "기술지표 채팅 처리 중 서버 내부 오류가 발생했습니다."}), 500


@app.route('/api/daily-pick', methods=['POST'])
def api_daily_pick():
    """
    [탭 4: 하루 1종목 추천]
    당일 시가총액 상위 후보 종목들의 실시간 시세·재무·수급·기술지표·뉴스·공시를 병렬 수집하고,
    3단계 폴백 모델(gemini-3.5-flash-lite -> 3.1-flash-lite -> gemma-4-26b-a4b-it)로
    가장 유망한 1종목과 진입가·예상판매가·선정이유·기업소개를 도출한다.
    """
    try:
        payload = request.get_json(silent=True) or {}
        force_refresh = bool(payload.get("force_refresh", False))
        beginner_mode = bool(payload.get("beginner_mode", False))
        today_str = datetime.now().strftime("%Y-%m-%d")

        if not force_refresh and _DAILY_PICK_CACHE["date"] == today_str and _DAILY_PICK_CACHE["data"]:
            logger.info(f"[DAILY_PICK] 당일 캐시 반환 ({today_str})")
            return jsonify(_DAILY_PICK_CACHE["data"]), 200

        logger.info(f"[REQUEST] POST /api/daily-pick (date={today_str}, force_refresh={force_refresh})")
        candidate_codes = fetch_daily_candidates(limit=4)

        def _gather_candidate(code):
            ev = collect_stock_data(code)
            tech = calculate_technical_indicators(code)
            snap = ev.get("snapshot") or {}
            cns = ev.get("consensus") or {}
            ts = tech.get("latest_summary") or {}
            return {
                "code": ev.get("code") or code,
                "name": ev.get("name") or code,
                "current_price": snap.get("current_price"),
                "change_rate": snap.get("change_rate"),
                "market_cap": snap.get("market_cap"),
                "per": snap.get("per"),
                "cns_per": snap.get("cns_per"),
                "pbr": snap.get("pbr"),
                "week52_high": snap.get("week52_high"),
                "week52_low": snap.get("week52_low"),
                "target_price_consensus": cns.get("target_price"),
                "investor_trends": (ev.get("investor_trends") or [])[:3],
                "annual_financials": (ev.get("financials") or [])[-2:],
                "technical": {
                    "ma5": ts.get("ma5"),
                    "ma20": ts.get("ma20"),
                    "ma60": ts.get("ma60"),
                    "rsi14": ts.get("rsi14"),
                    "macd": ts.get("macd"),
                    "macd_signal": ts.get("macd_signal"),
                    "bb_upper": ts.get("bb_upper"),
                    "bb_lower": ts.get("bb_lower"),
                },
                "news_titles": [n.get("title") for n in (ev.get("news") or [])[:4]],
                "dart_titles": [d.get("title") for d in (ev.get("dart_disclosures") or [])[:3]],
            }

        candidates_data = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_gather_candidate, c) for c in candidate_codes]
            for fut in futures:
                try:
                    item = fut.result()
                    if item.get("current_price"):
                        candidates_data.append(item)
                except Exception as e:
                    logger.warning(f"[DAILY_PICK] 후보 수집 중 일부 스킵: {e}")

        if not candidates_data:
            return jsonify({"error": "당일 후보 종목 데이터를 수집하지 못했습니다."}), 500

        tone_note = (
            "초보자도 쉽게 이해할 수 있도록 어려운 용어에는 괄호로 쉬운 비유를 붙여 작성하세요."
            if beginner_mode
            else "깔끔하고 논리적인 리서치 리포트 톤으로 작성하세요."
        )

        prompt = f"""당신은 수석 퀀트·펀더멘털 애널리스트입니다.
오늘({today_str}) 실제 수집된 아래 {len(candidates_data)}개 후보 종목의 데이터(시세, 밸류에이션, 실적, 수급, 기술적 지표, 최신 뉴스, 공시)를 모두 종합 비교하여, **오늘 가장 투자 매력도가 높은 단 1개의 종목**을 선정하세요.

[선정 및 응답 규칙]
1. 반드시 제공된 JSON 데이터에 존재하는 후보 종목 중 정확히 1개만 선택하고, 없는 숫자를 지어내지 마세요.
2. 진입가격(`entry_price`), 예상 판매가/목표가(`target_price`), 손절 기준가(`stop_loss_price`)는 현재가, 이동평균선(MA5/20/60), 볼린저밴드 상/하단, 증권사 컨센서스 목표주가를 근거로 현실적이고 구체적인 원(KRW) 단위 정수로 제시하세요.
3. {tone_note}
4. 응답은 마크다운 설명 없이 **반드시 아래 JSON 형식만** 출력하세요:

{{
  "selected_code": "6자리 종목코드",
  "selected_name": "정식 종목명",
  "current_price": 191500,
  "entry_price": 188000,
  "target_price": 235000,
  "stop_loss_price": 176000,
  "expected_return_pct": 25.0,
  "company_intro": "기업의 핵심 사업, 시장 지위, 주요 수익원을 3~4문장으로 알기 쉽게 소개",
  "selection_reasons": [
    "1. 밸류에이션/실적 근거: 구체적 수치 인용",
    "2. 수급/시장 흐름 근거: 외인·기관 수급 및 목표주가 인용",
    "3. 기술적 위치 근거: RSI, 이평선, 볼린저밴드 수치 인용",
    "4. 뉴스·공시 모멘텀 근거: 최신 이슈 인용"
  ],
  "price_rationale": "진입가격과 예상판매가(목표가), 손절가를 해당 가격대로 설정한 기술적·수급적 근거 2~3문장",
  "risk_note": "투자 시 주의해야 할 핵심 리스크 1~2문장",
  "candidates_summary": [
    {{"code": "코드", "name": "종목명", "current_price": 0, "per": 0, "rsi14": 0, "comment": "선정 또는 제외 사유 한 줄 요약"}}
  ]
}}

[당일 후보 종목 실제 수집 데이터 JSON]
{json.dumps(candidates_data, ensure_ascii=False)}
"""

        fb = call_gemini_with_fallback(prompt)
        if fb.get("error"):
            return jsonify({"error": fb["error"]}), 502

        raw_text = fb["answer"].strip()
        # ```json ... ``` 마크다운 펜스 제거 후 JSON 파싱
        cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            pick_result = json.loads(cleaned_json)
        except ValueError:
            # 중괄호 블록 추출 재시도
            m = re.search(r"\{.*\}", cleaned_json, re.DOTALL)
            if not m:
                logger.error(f"[ERROR] 하루 1종목 추천 JSON 파싱 실패: {raw_text[:200]}")
                return jsonify({"error": "추천 결과 형식 변환에 실패했습니다. 다시 시도해 주세요."}), 500
            pick_result = json.loads(m.group(0))

        final_payload = {
            "date": today_str,
            "model": fb["model"],
            "pick": pick_result,
            "candidates_raw": [
                {
                    "code": c["code"],
                    "name": c["name"],
                    "current_price": c["current_price"],
                    "change_rate": c["change_rate"],
                    "per": c["per"],
                    "rsi14": c["technical"].get("rsi14"),
                }
                for c in candidates_data
            ],
        }

        _DAILY_PICK_CACHE["date"] = today_str
        _DAILY_PICK_CACHE["data"] = final_payload

        logger.info(
            f"[RESPONSE] 하루 1종목 추천 완료 (selected={pick_result.get('selected_name')}, "
            f"model={fb['model']})"
        )
        return jsonify(final_payload), 200

    except Exception:
        logger.error("[ERROR] POST /api/daily-pick 처리 중 오류 발생", exc_info=True)
        return jsonify({"error": "하루 1종목 추천 생성 중 서버 오류가 발생했습니다."}), 500


if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info("[START] Flask 서버 시작 (http://127.0.0.1:5000)")
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
