import os
import json
import logging
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from google import genai
from collector import collect_stock_data

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
    """메인 웹페이지 (추후 Step 17에서 완성될 index.html 렌더링)"""
    try:
        return render_template('index.html')
    except Exception:
        # Step 17 이전 index.html이 아직 없을 때를 위한 기본 응답
        return "<h1>Stock Data Collector & Briefing Tool</h1><p>Server is running. Check <a href='/health'>/health</a></p>"

@app.route('/health', methods=['GET'])
def health():
    """
    서버 상태 및 필수 API Key 설정 여부만 확인하여 반환
    (보안 절대 규칙: 실제 키 값은 절대 외부로 노출하지 않음)
    """
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    is_serper_ready = bool(serper_key)
    is_gemini_ready = bool(gemini_key)

    logger.info(f"[HEALTH] 상태 점검 요청 - Serper설정: {is_serper_ready}, Gemini설정: {is_gemini_ready}")

    return jsonify({
        "status": "ok",
        "serper_api_configured": is_serper_ready,
        "gemini_api_configured": is_gemini_ready,
        "gemini_model": gemini_model
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


if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info("[START] Flask 서버 시작 (http://127.0.0.1:5000)")
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
