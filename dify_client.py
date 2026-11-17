import os
import logging
import requests

logger = logging.getLogger(__name__)

DEFAULT_DIFY_BASE_URL = "https://api.dify.ai/v1"
DIFY_TIMEOUT = 45


def call_dify_chatbot(bot_type, query, conversation_id="", user_id="investment-assistant-user", inputs=None):
    """
    Dify 기본 챗봇 앱의 백엔드 서비스 API(/chat-messages)를 호출한다.

    Args:
        bot_type (str): "analysis" (탭1 종목분석 챗봇①) 또는 "tech" (탭3 기술지표 챗봇③)
        query (str): 근거 데이터 컨텍스트와 사용자 질문이 결합된 프롬프트 문자열
        conversation_id (str): 이어갈 대화 ID (새 대화면 빈 문자열)
        user_id (str): Dify 사용자 식별자
        inputs (dict): 선택적 오케스트레이트 변수 딕셔너리

    Returns:
        dict: {
            "answer": str,
            "conversation_id": str,
            "error": str | None
        }
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

    logger.info(f"[DIFY] Dify 챗봇 API 호출 시작 (bot={bot_type}, conversation_id={conversation_id or 'NEW'})")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=DIFY_TIMEOUT)

        if resp.status_code == 401:
            msg = f"Dify API Key({key_name}) 인증에 실패했습니다 (401). 키 값을 확인해 주세요."
            logger.error(f"[ERROR] Dify 401 인증 오류 (bot={bot_type})")
            return {"answer": "", "conversation_id": conversation_id, "error": msg}

        if resp.status_code == 404:
            msg = "Dify 챗봇 앱 또는 대화 세션을 찾을 수 없습니다 (404)."
            logger.error(f"[ERROR] Dify 404 응답 (bot={bot_type})")
            return {"answer": "", "conversation_id": conversation_id, "error": msg}

        if resp.status_code != 200:
            err_detail = ""
            try:
                err_detail = resp.json().get("message", "")
            except ValueError:
                err_detail = resp.text[:150]
            msg = f"Dify API 호출 실패 (상태코드: {resp.status_code}) {err_detail}".strip()
            logger.error(f"[ERROR] Dify 비정상 응답({resp.status_code}, bot={bot_type}): {err_detail}")
            return {"answer": "", "conversation_id": conversation_id, "error": msg}

        data = resp.json()
        answer = data.get("answer", "")
        new_conv_id = data.get("conversation_id", conversation_id)

        logger.info(f"[DIFY] Dify 챗봇 응답 수신 완료 (bot={bot_type}, conv_id={new_conv_id}, len={len(answer)})")
        return {
            "answer": answer,
            "conversation_id": new_conv_id,
            "error": None,
        }

    except requests.exceptions.Timeout:
        msg = "Dify 챗봇 응답 시간이 초과되었습니다 (네트워크 타임아웃)."
        logger.error(f"[ERROR] Dify API 타임아웃 (bot={bot_type})", exc_info=True)
        return {"answer": "", "conversation_id": conversation_id, "error": msg}
    except requests.exceptions.RequestException as e:
        msg = f"Dify 서버 연결 중 네트워크 오류가 발생했습니다: {e}"
        logger.error(f"[ERROR] Dify API 요청 실패 (bot={bot_type})", exc_info=True)
        return {"answer": "", "conversation_id": conversation_id, "error": msg}
    except ValueError:
        msg = "Dify API 응답을 JSON으로 변환하는 데 실패했습니다."
        logger.error(f"[ERROR] Dify API JSON 변환 실패 (bot={bot_type})", exc_info=True)
        return {"answer": "", "conversation_id": conversation_id, "error": msg}
