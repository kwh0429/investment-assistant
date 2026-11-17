import os
import json
import logging
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from google import genai
from collector import collect_stock_data
from quant import calculate_rebalancing, calculate_technical_indicators
from dify_client import call_dify_chatbot

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

    is_serper_ready = bool(serper_key)
    is_gemini_ready = bool(gemini_key)
    is_dify_analysis_ready = bool(dify_analysis_key)
    is_dify_tech_ready = bool(dify_tech_key)

    logger.info(
        f"[HEALTH] 상태 점검 요청 - Serper: {is_serper_ready}, Gemini: {is_gemini_ready}, "
        f"Dify분석: {is_dify_analysis_ready}, Dify기술: {is_dify_tech_ready}"
    )

    return jsonify({
        "status": "ok",
        "serper_api_configured": is_serper_ready,
        "gemini_api_configured": is_gemini_ready,
        "gemini_model": gemini_model,
        "dify_analysis_api_configured": is_dify_analysis_ready,
        "dify_tech_api_configured": is_dify_tech_ready,
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

    try:
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model=gemini_model,
            contents=prompt,
        )
        brief_text = response.text or ""
        logger.info(f"[RESPONSE] AI 브리핑 생성 완료 (target={stock_name}, length={len(brief_text)})")
        return jsonify({
            "model": gemini_model,
            "briefing": brief_text,
        }), 200

    except Exception as e:
        logger.error("[ERROR] Gemini 브리핑 생성 실패", exc_info=True)
        return jsonify({
            "error": f"AI 브리핑 생성 중 오류가 발생했습니다: {str(e)}"
        }), 500


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
        conversation_id = str(payload.get("conversation_id") or "").strip()

        logger.info(f"[REQUEST] POST /api/chat/analysis action={action}, code={code}, use_tool={use_tool}")

        evidence = None
        collect_errors = []

        if action == "init":
            if not code:
                return jsonify({"error": "종목코드 6자리를 입력해 주세요."}), 400

            if use_tool:
                evidence = collect_stock_data(code)
                collect_errors = evidence.get("errors", [])
                if not evidence.get("name") and not evidence.get("snapshot") and collect_errors:
                    return jsonify({"error": collect_errors[0], "evidence": evidence}), 400

                stock_name = evidence.get("name") or code
                full_query = (
                    f"당신은 사용자 전담 개인 PB(Private Banker)입니다. 아래 제공된 '{stock_name}({code})'의 "
                    "실제 수집 데이터(JSON)만을 근거로, 고객을 위한 명확하고 실전적인 심층분석 리포트와 투자 전략을 작성해 주세요.\n\n"
                    "[개인 PB 작성 원칙]\n"
                    "1. '투자는 본인 책임', '상황에 따라 다릅니다' 같은 원론적이고 회피성 멘트는 절대 쓰지 마세요.\n"
                    "2. 데이터에 기반해 현재 포지션 의견(예: 비중 확대 / 보유 유지 / 분할 익절 / 관망 / 손절 주의)을 명확히 제시하세요.\n"
                    "3. 주요 지지선(52주 최저·단기 저점)과 저항선(전고점 등)을 기준으로 구체적인 대응 전략을 제안하세요.\n"
                    "4. 연간 실적에서 `is_consensus: true`인 연도는 확정 실적이 아닌 '증권사 예상치(E)'임을 반드시 구분해 서술하세요.\n"
                    "5. 금액은 조/억 단위로 환산하고, 표·글머리 기호 등 깔끔한 마크다운으로 정리하세요.\n\n"
                    "[필수 분석 목차]\n"
                    "### 1. PB 종합 의견 (현재 스탠스 및 핵심 이유 1줄 요약)\n"
                    "### 2. 기업 체급 및 시장 수급 (시가총액, 52주 위치, 외국인 소진율, 거래대금)\n"
                    "### 3. 실적 성장성과 수익성 (매출·영업이익 추이, 영업이익률, ROE 변화)\n"
                    "### 4. 밸류에이션 및 재무 건전성 (현재 PER vs 추정 PER, PBR, 부채비율, 배당)\n"
                    "### 5. 최신 뉴스 핵심 이슈 및 구체적 대응 전략\n\n"
                    f"[수집된 근거 데이터 JSON]\n{json.dumps(evidence, ensure_ascii=False)}"
                )
            else:
                full_query = (
                    f"당신은 개인 전담 PB입니다. 종목코드 '{code}'에 대해 "
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
            )

            if not conversation_id and use_tool and code:
                # '조회'를 누르지 않고 바로 질문한 경우에도 근거 수집 후 PB 프롬프트 적용
                evidence = collect_stock_data(code)
                collect_errors = evidence.get("errors", [])
                full_query = (
                    f"[근거 데이터 JSON]\n{json.dumps(evidence, ensure_ascii=False)}\n\n"
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
                "collect_errors": collect_errors,
            }), 502 if "API Key" not in dify_res["error"] else 400

        logger.info(f"[RESPONSE] 탭1 심층분석 완료 (action={action}, code={code}, use_tool={use_tool})")
        return jsonify({
            "answer": dify_res["answer"],
            "conversation_id": dify_res["conversation_id"],
            "use_tool": use_tool,
            "evidence": evidence,
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

        full_query = (
            "[계산된 최신 기술지표 숫자 (FinanceDataReader 기반)]\n"
            f"종목코드: {code}\n"
            f"{json.dumps(summary, ensure_ascii=False)}\n\n"
            "[지침]\n"
            "오직 위에 제공된 숫자만을 근거로 현재 이동평균선 배열, RSI 수준, MACD 추세, 볼린저밴드 위치를 "
            "초보자가 이해하기 쉽게 설명하세요. 매수/매도 추천은 하지 마세요.\n\n"
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


if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info("[START] Flask 서버 시작 (http://127.0.0.1:5000)")
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
