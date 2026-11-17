import os
import re
import logging
import requests

logger = logging.getLogger(__name__)

# 네이버 금융 모바일/내부 REST API 기본 URL 및 매너 헤더
NAVER_API_BASE = "https://m.stock.naver.com/api/stock"
SERPER_NEWS_URL = "https://google.serper.dev/news"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}
REQUEST_TIMEOUT = 10


def _clean_number(value, as_float=False):
    """
    텍스트 숫자(예: '1,102,000', '-1.61')에서 콤마 등을 제거하고 순수 숫자로 변환.
    변환할 수 없는 값('-', None, 빈 문자열 등)은 None 반환.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "N/A"):
        return None

    # 부호와 숫자, 소수점만 추출
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None

    num_str = match.group(0)
    try:
        if as_float or "." in num_str:
            return float(num_str)
        return int(num_str)
    except ValueError:
        return None


def _parse_korean_amount(value):
    """
    한글 금액 단위('891조 9,312억', '5조 783억', '9,850억')를
    순수 원(KRW) 단위 정수로 변환. 변환 불가 시 None 반환.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "N/A"):
        return None

    total = 0
    matched = False

    jo_match = re.search(r"(\d+)\s*조", text)
    if jo_match:
        total += int(jo_match.group(1)) * 1_0000_0000_0000
        matched = True

    eok_match = re.search(r"(\d+)\s*억", text)
    if eok_match:
        total += int(eok_match.group(1)) * 1_0000_0000
        matched = True

    man_match = re.search(r"(\d+)\s*만", text)
    if man_match:
        total += int(man_match.group(1)) * 1_0000
        matched = True

    if matched:
        return total
    return _clean_number(text)


def _request_naver_json(url, errors):
    """네이버 금융 API에 GET 요청을 보내고 JSON 딕셔너리를 반환"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            errors.append("네이버 금융에서 해당 종목을 찾을 수 없습니다 (404).")
            logger.error(f"[ERROR] 네이버 금융 API 404 응답: {url}")
            return None
        if resp.status_code != 200:
            errors.append(f"네이버 금융 API 접근 실패 (상태코드: {resp.status_code}).")
            logger.error(f"[ERROR] 네이버 금융 API 비정상 응답({resp.status_code}): {url}")
            return None

        return resp.json()

    except requests.exceptions.Timeout:
        errors.append("네이버 금융 서버 응답 시간이 초과되었습니다 (네트워크 타임아웃).")
        logger.error(f"[ERROR] 네이버 금융 API 타임아웃: {url}", exc_info=True)
    except requests.exceptions.RequestException as e:
        errors.append(f"네이버 금융 연결 중 네트워크 오류가 발생했습니다: {e}")
        logger.error(f"[ERROR] 네이버 금융 요청 실패: {url}", exc_info=True)
    except ValueError:
        errors.append("네이버 금융 응답을 JSON으로 변환하는 데 실패했습니다.")
        logger.error(f"[ERROR] 네이버 금융 JSON 변환 실패: {url}", exc_info=True)

    return None


def fetch_basic_quote(code, errors):
    """
    [1구역] 네이버 금융 basic + integration API에서
    정식 종목명과 상단 시세(현재가, 등락률, 시가/고가/저가/거래량)를 파싱
    """
    logger.info(f"[NAVER] 네이버 금융 API 접근 시작 (User-Agent 적용, code={code})")

    basic_url = f"{NAVER_API_BASE}/{code}/basic"
    integration_url = f"{NAVER_API_BASE}/{code}/integration"

    basic_data = _request_naver_json(basic_url, errors)
    integration_data = _request_naver_json(integration_url, errors)

    if not basic_data and not integration_data:
        return None, {}

    # 1. 정식 종목명 추출
    name = None
    if basic_data:
        name = basic_data.get("stockName")
    if not name and integration_data:
        name = integration_data.get("stockName")

    if not name:
        errors.append("네이버 금융 데이터에서 정식 종목명을 찾지 못했습니다.")
        logger.error(f"[ERROR] 종목명 파싱 실패 (code={code})")

    # 2. 상단 시세 추출 (basic API)
    current_price = _clean_number(basic_data.get("closePrice")) if basic_data else None
    change_rate = _clean_number(basic_data.get("fluctuationsRatio"), as_float=True) if basic_data else None

    # 3. 일중 시세 상세 추출 (integration API -> totalInfos)
    info_map = {}
    if integration_data and isinstance(integration_data.get("totalInfos"), list):
        info_map = {
            item.get("code"): item.get("value")
            for item in integration_data["totalInfos"]
            if item.get("code")
        }

    snapshot = {
        # [1구역] 상단 시세
        "current_price": current_price,
        "change_rate": change_rate,
        "prev_close": _clean_number(info_map.get("lastClosePrice")),
        "open_price": _clean_number(info_map.get("openPrice")),
        "high_price": _clean_number(info_map.get("highPrice")),
        "low_price": _clean_number(info_map.get("lowPrice")),
        "volume": _clean_number(info_map.get("accumulatedTradingVolume")),
        "trading_value": _parse_korean_amount(info_map.get("accumulatedTradingValue")),
        # [2구역] 투자정보 (밸류에이션 및 수급/배당)
        "market_cap": _parse_korean_amount(info_map.get("marketValue")),
        "foreign_rate": _clean_number(info_map.get("foreignRate"), as_float=True),
        "week52_high": _clean_number(info_map.get("highPriceOf52Weeks")),
        "week52_low": _clean_number(info_map.get("lowPriceOf52Weeks")),
        "per": _clean_number(info_map.get("per"), as_float=True),
        "eps": _clean_number(info_map.get("eps")),
        "cns_per": _clean_number(info_map.get("cnsPer"), as_float=True),
        "cns_eps": _clean_number(info_map.get("cnsEps")),
        "pbr": _clean_number(info_map.get("pbr"), as_float=True),
        "bps": _clean_number(info_map.get("bps")),
        "dividend_yield": _clean_number(info_map.get("dividendYieldRatio"), as_float=True),
        "dividend": _clean_number(info_map.get("dividend")),
    }

    logger.info(
        f"[PARSE] 스냅샷 파싱 완료 (name={name}, current_price={current_price}, "
        f"per={snapshot['per']}, pbr={snapshot['pbr']})"
    )
    return name, snapshot


def fetch_financials(code, errors):
    """
    [3구역] 네이버 금융 finance/annual API에서
    연간 기업실적분석(매출액, 영업이익, 순이익, 영업이익률, ROE, 부채비율 등)을 파싱하고
    (E) 추정치 여부에 따라 is_consensus 신호등(True/False)을 부여
    """
    annual_url = f"{NAVER_API_BASE}/{code}/finance/annual"
    data = _request_naver_json(annual_url, errors)
    if not data:
        return []

    finance_info = data.get("financeInfo")
    if not finance_info:
        errors.append("네이버 금융 실적 데이터 구조가 예상과 다릅니다.")
        logger.error(f"[ERROR] financeInfo 키 누락 (code={code})")
        return []

    tr_titles = finance_info.get("trTitleList", [])
    row_list = finance_info.get("rowList", [])

    # 행 제목 -> columns 딕셔너리 매핑
    row_map = {
        row.get("title"): row.get("columns", {})
        for row in row_list
        if row.get("title")
    }

    def _get_eok_value(metric_name, period_key):
        """억원 단위 재무 항목을 원(KRW) 단위 정수로 변환"""
        raw = row_map.get(metric_name, {}).get(period_key, {}).get("value")
        num = _clean_number(raw)
        return num * 1_0000_0000 if num is not None else None

    def _get_ratio_value(metric_name, period_key):
        """비율/배수 항목을 float 숫자로 변환"""
        raw = row_map.get(metric_name, {}).get(period_key, {}).get("value")
        return _clean_number(raw, as_float=True)

    financials = []
    for p in tr_titles:
        p_key = p.get("key")
        p_title = (p.get("title") or "").strip().rstrip(".")
        is_consensus = (p.get("isConsensus") == "Y")

        if not p_key or not p_title:
            continue

        financials.append({
            "period": p_title,
            "period_type": "annual",
            "is_consensus": is_consensus,
            "revenue": _get_eok_value("매출액", p_key),
            "operating_income": _get_eok_value("영업이익", p_key),
            "net_income": _get_eok_value("당기순이익", p_key),
            "operating_margin": _get_ratio_value("영업이익률", p_key),
            "roe": _get_ratio_value("ROE", p_key),
            "debt_ratio": _get_ratio_value("부채비율", p_key),
            "per": _get_ratio_value("PER", p_key),
            "pbr": _get_ratio_value("PBR", p_key),
        })

    logger.info(f"[PARSE] 연간 실적 파싱 완료 (code={code}, 기간수={len(financials)})")
    return financials


def fetch_news(stock_name, errors):
    """
    파싱된 정식 종목명을 사용하여 Serper News API에서 최신 뉴스 5건을 조회.
    종목명이 없거나 API Key가 미설정된 경우 안전하게 건너뛰고 errors에 기록.
    """
    if not stock_name:
        errors.append("정식 종목명을 확보하지 못해 뉴스 검색을 건너뛰었습니다.")
        logger.warning("[SERPER] 종목명 누락으로 뉴스 검색 스킵")
        return []

    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    if not serper_key:
        errors.append("Serper API Key가 설정되지 않아 뉴스를 수집할 수 없습니다 (.env 확인 필요).")
        logger.error("[ERROR] SERPER_API_KEY 미설정")
        return []

    logger.info(f"[SERPER] Serper News API 호출 시작 (query={stock_name})")

    headers = {
        "X-API-KEY": serper_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": stock_name,
        "gl": "kr",
        "hl": "ko",
        "num": 5,
    }

    try:
        resp = requests.post(SERPER_NEWS_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            errors.append(f"Serper News API 호출 실패 (상태코드: {resp.status_code}).")
            logger.error(f"[ERROR] Serper News API 비정상 응답({resp.status_code})")
            return []

        data = resp.json()
        raw_news = data.get("news", [])

        news_list = []
        for item in raw_news[:5]:
            news_list.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "description": item.get("snippet", ""),
                "published": item.get("date", ""),
                "source": item.get("source", ""),
            })

        logger.info(f"[SERPER] 뉴스 수집 완료 (query={stock_name}, 건수={len(news_list)})")
        return news_list

    except requests.exceptions.Timeout:
        errors.append("Serper News API 응답 시간이 초과되었습니다 (네트워크 타임아웃).")
        logger.error("[ERROR] Serper News API 타임아웃", exc_info=True)
    except requests.exceptions.RequestException as e:
        errors.append(f"Serper News API 연결 중 네트워크 오류가 발생했습니다: {e}")
        logger.error("[ERROR] Serper News API 요청 실패", exc_info=True)
    except ValueError:
        errors.append("Serper News API 응답을 JSON으로 변환하는 데 실패했습니다.")
        logger.error("[ERROR] Serper News API JSON 변환 실패", exc_info=True)

    return []


def collect_stock_data(code):
    """종목코드를 검증하고 전체 주식 데이터를 수집하여 규격 JSON(dict) 반환"""
    errors = []
    clean_code = (code or "").strip()

    if not clean_code:
        errors.append("종목코드가 입력되지 않았습니다.")
        return {
            "code": "",
            "name": None,
            "snapshot": {},
            "financials": [],
            "news": [],
            "errors": errors,
        }

    if not re.fullmatch(r"\d{6}", clean_code):
        errors.append("종목코드는 6자리 숫자여야 합니다. (예: 000660)")
        return {
            "code": clean_code,
            "name": None,
            "snapshot": {},
            "financials": [],
            "news": [],
            "errors": errors,
        }

    name, snapshot = fetch_basic_quote(clean_code, errors)
    financials = fetch_financials(clean_code, errors)
    news = fetch_news(name, errors)

    return {
        "code": clean_code,
        "name": name,
        "snapshot": snapshot,
        "financials": financials,
        "news": news,
        "errors": errors,
    }
