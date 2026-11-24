import os
import time
import logging
import requests
from google import genai

logger = logging.getLogger(__name__)

DEFAULT_DIFY_BASE_URL = "https://api.dify.ai/v1"
DEFAULT_PRIMARY_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_MODELS = "gemini-3.1-flash-lite,gemma-4-26b-a4b-it"
DIFY_TIMEOUT = 10


def get_gemini_model_chain():
    """
    1순위 기본 모델 + 다음 순위 폴백 모델 목록 반환
    (기본: gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemma-4-26b-a4b-it)
    """
    primary = os.getenv("GEMINI_MODEL", DEFAULT_PRIMARY_MODEL).strip() or DEFAULT_PRIMARY_MODEL
    raw_fallbacks = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    fallbacks = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]

    chain = []
    for m in [primary] + fallbacks:
        if m not in chain:
            chain.append(m)
    return chain


def call_gemini_with_fallback(prompt, config=None):
    """
    Google AI Studio API를 1순위 -> 2순위 -> 3순위 모델 순서로 호출한다.
    문제가 생기면 자동으로 다음 순위 모델로 전환한다.
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return {
            "answer": "",
            "model": None,
            "error": "Gemini API Key가 설정되지 않았습니다 (.env 확인 필요).",
        }

    model_chain = get_gemini_model_chain()
    client = genai.Client(api_key=gemini_key, http_options={"timeout": 10000})
    last_error = ""

    for idx, model_name in enumerate(model_chain):
        try:
            logger.info(f"[GEMINI] 모델 호출 시도 ({idx + 1}/{len(model_chain)}): {model_name}")
            kwargs = {"model": model_name, "contents": prompt}
            if config:
                req_cfg = dict(config)
                if model_name.startswith("gemma"):
                    req_cfg.pop("response_mime_type", None)
                kwargs["config"] = req_cfg
            response = client.models.generate_content(**kwargs)
            text = (response.text or "").strip()
            if text:
                logger.info(f"[GEMINI] 응답 생성 성공 (model={model_name}, len={len(text)})")
                return {"answer": text, "model": model_name, "error": None}
            last_error = f"{model_name} 응답이 비어 있습니다."
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[GEMINI] {model_name} 호출 실패 -> 다음 모델 확인 ({last_error[:140]})")

    logger.error(f"[ERROR] 모든 Gemini/Gemma 폴백 모델 호출 실패: {last_error}")
    return {
        "answer": "",
        "model": None,
        "error": f"AI 모델 응답 생성에 실패했습니다: {last_error}",
    }


def _is_transient_overload(status_code, err_text):
    """일시적 트래픽 과부하 또는 서버 장애 여부 판단"""
    if status_code in (429, 500, 502, 503, 504):
        return True
    lower = (err_text or "").lower()
    return any(k in lower for k in ("high demand", "overloaded", "unavailable", "rate limit", "temporarily"))


def call_dify_chatbot(bot_type, query, conversation_id="", user_id="investment-assistant-user", inputs=None):
    """
    Dify 기본 챗봇 앱의 백엔드 서비스 API(/chat-messages)를 호출한다.
    - 일시적 과부하(high demand / 503 등) 발생 시 1회 자동 재시도
    - 재시도에도 Dify 내부 모델 장애가 지속되면 Gemini 폴백 체인(3.5-flash-lite -> 3.1-flash-lite -> gemma-4-26b-a4b-it)으로 자동 우회
    """
    base_url = os.getenv("DIFY_BASE_URL", DEFAULT_DIFY_BASE_URL).strip().rstrip("/")

    if bot_type == "analysis":
        api_key = os.getenv("DIFY_ANALYSIS_API_KEY", "").strip()
        key_name = "DIFY_ANALYSIS_API_KEY"
    elif bot_type == "tech":
        api_key = os.getenv("DIFY_TECH_API_KEY", "").strip()
        key_name = "DIFY_TECH_API_KEY"
    else:
        return {"answer": "", "conversation_id": conversation_id, "error": f"알 수 없는 챗봇 유형입니다: {bot_type}"}

    if not api_key:
        msg = f"Dify 챗봇 API Key({key_name})가 설정되지 않았습니다 (.env 파일을 확인해 주세요)."
        logger.error(f"[ERROR] {key_name} 미설정")
        return {"answer": "", "conversation_id": conversation_id, "error": msg}

    clean_query = (query or "").strip()
    if not clean_query:
        return {"answer": "", "conversation_id": conversation_id, "error": "질문 내용이 비어 있습니다."}

    url = f"{base_url}/chat-messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs if isinstance(inputs, dict) else {},
        "query": clean_query,
        "response_mode": "blocking",
        "conversation_id": conversation_id or "",
        "user": user_id,
    }

    last_err_msg = ""
    should_fallback_to_gemini = False

    logger.info(
        f"[DIFY] Dify 챗봇 API 호출 시작 (bot={bot_type}, conversation_id={conversation_id or 'NEW'})"
    )
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=DIFY_TIMEOUT)

        if resp.status_code == 200:
            data = resp.json()
            answer = data.get("answer", "")
            new_conv_id = data.get("conversation_id", conversation_id)
            logger.info(f"[DIFY] Dify 챗봇 응답 수신 완료 (bot={bot_type}, conv_id={new_conv_id}, len={len(answer)})")
            return {"answer": answer, "conversation_id": new_conv_id, "error": None}

        if resp.status_code == 401:
            msg = f"Dify API Key({key_name}) 인증에 실패했습니다 (401). 키 값을 확인해 주세요."
            logger.error(f"[ERROR] Dify 401 인증 오류 (bot={bot_type})")
            return {"answer": "", "conversation_id": conversation_id, "error": msg}

        if resp.status_code == 404:
            msg = "Dify 챗봇 앱 또는 대화 세션을 찾을 수 없습니다 (404)."
            logger.error(f"[ERROR] Dify 404 응답 (bot={bot_type})")
            return {"answer": "", "conversation_id": conversation_id, "error": msg}

        err_detail = ""
        try:
            err_detail = resp.json().get("message", "")
        except ValueError:
            err_detail = resp.text[:150]

        last_err_msg = f"Dify API 호출 실패 (상태코드: {resp.status_code}) {err_detail}".strip()
        logger.warning(f"[DIFY] 비정상 응답({resp.status_code}): {err_detail}")

        if _is_transient_overload(resp.status_code, err_detail):
            should_fallback_to_gemini = True

    except requests.exceptions.Timeout:
        last_err_msg = "Dify 챗봇 응답 시간 초과"
        logger.warning(f"[DIFY] 타임아웃 ({DIFY_TIMEOUT}s, bot={bot_type}) -> 백업 모델 전환")
        should_fallback_to_gemini = True
    except requests.exceptions.RequestException as e:
        last_err_msg = f"Dify 서버 연결 중 네트워크 오류가 발생했습니다: {e}"
        logger.error(f"[ERROR] Dify API 요청 실패 (bot={bot_type})", exc_info=True)
        should_fallback_to_gemini = True
    except ValueError:
        last_err_msg = "Dify API 응답을 JSON으로 변환하는 데 실패했습니다."
        logger.error(f"[ERROR] Dify API JSON 변환 실패 (bot={bot_type})", exc_info=True)

    # Dify가 일시적 과부하로 실패한 경우 -> Gemini 폴백 체인으로 자동 우회
    if should_fallback_to_gemini and os.getenv("GEMINI_API_KEY", "").strip():
        logger.info(f"[DIFY->GEMINI] Dify 과부하 감지, 백업 모델 체인으로 자동 전환 (bot={bot_type})")
        fb = call_gemini_with_fallback(clean_query)
        if not fb.get("error"):
            return {
                "answer": fb["answer"],
                "conversation_id": conversation_id,
                "fallback_model": fb["model"],
                "error": None,
            }
        last_err_msg = f"{last_err_msg} | 백업 모델 실패: {fb['error']}"

    return {"answer": "", "conversation_id": conversation_id, "error": last_err_msg}
