import os
import re
import io
import zipfile
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 네이버 금융 모바일/내부 REST API 기본 URL 및 매너 헤더
NAVER_API_BASE = "https://m.stock.naver.com/api/stock"
NAVER_INDEX_BASE = "https://m.stock.naver.com/api/index"
NAVER_AC_URL = "https://ac.stock.naver.com/ac"
SERPER_NEWS_URL = "https://google.serper.dev/news"
EZINIT_ANALYSIS_URL = "https://portfolio.ezinit.com/community/stock_analysis"
DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"

# OpenDART 종목코드 -> 고유번호(8자리) 메모리 캐시
_DART_CORP_CACHE = {}
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

    # 4. 네이버 integration 추가 데이터 (목표주가, 수급동향, 증권사 리포트, 동일업종 비교)
    extra_naver = {
        "consensus": {},
        "investor_trends": [],
        "researches": [],
        "peers": [],
    }

    if integration_data:
        cns = integration_data.get("consensusInfo") or {}
        if cns:
            extra_naver["consensus"] = {
                "target_price": _clean_number(cns.get("priceTargetMean")),
                "recomm_score": _clean_number(cns.get("recommMean"), as_float=True),
                "date": cns.get("createDate"),
            }

        for dt in (integration_data.get("dealTrendInfos") or [])[:5]:
            extra_naver["investor_trends"].append({
                "date": dt.get("bizdate"),
                "close": _clean_number(dt.get("closePrice")),
                "foreigner_net": _clean_number(dt.get("foreignerPureBuyQuant")),
                "organ_net": _clean_number(dt.get("organPureBuyQuant")),
                "individual_net": _clean_number(dt.get("individualPureBuyQuant")),
                "foreigner_ratio": _clean_number(dt.get("foreignerHoldRatio"), as_float=True),
            })

        for r in (integration_data.get("researches") or [])[:5]:
            extra_naver["researches"].append({
                "firm": r.get("bnm", ""),
                "title": r.get("tit", ""),
                "date": r.get("wdt", ""),
            })

        for p in (integration_data.get("industryCompareInfo") or [])[:5]:
            extra_naver["peers"].append({
                "code": p.get("itemCode", ""),
                "name": p.get("stockName", ""),
                "close": _clean_number(p.get("closePrice")),
                "change_rate": _clean_number(p.get("fluctuationsRatio"), as_float=True),
            })

    return name, snapshot, extra_naver


def fetch_financials(code, errors, period_type="annual"):
    """
    [3구역] 네이버 금융 finance/{annual|quarter} API에서
    기업실적분석(매출액, 영업이익, 순이익, 영업이익률, ROE, 부채비율 등)을 파싱하고
    (E) 추정치 여부에 따라 is_consensus 신호등(True/False)을 부여
    """
    endpoint = "annual" if period_type == "annual" else "quarter"
    url = f"{NAVER_API_BASE}/{code}/finance/{endpoint}"
    data = _request_naver_json(url, errors)
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
            "period_type": endpoint,
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

    logger.info(f"[PARSE] {endpoint} 실적 파싱 완료 (code={code}, 기간수={len(financials)})")
    return financials


def fetch_market_indices():
    """
    네이버 금융 국내증시 메인(/market/stock/kr)의
    코스피(KOSPI) 및 코스닥(KOSDAQ) 현재 지수와 등락률을 수집
    """
    indices = {}
    for idx_code in ("KOSPI", "KOSDAQ"):
        url = f"{NAVER_INDEX_BASE}/{idx_code}/basic"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                indices[idx_code] = {
                    "name": data.get("stockName", idx_code),
                    "value": _clean_number(data.get("closePrice"), as_float=True),
                    "change_rate": _clean_number(data.get("fluctuationsRatio"), as_float=True),
                }
        except Exception as e:
            logger.warning(f"[NAVER] {idx_code} 지수 조회 실패: {e}")

    logger.info(f"[NAVER] 시장 지수 수집 완료: {list(indices.keys())}")
    return indices


def fetch_ezinit_analysis(code):
    """
    우쎔AI 종목분석(https://portfolio.ezinit.com/community/stock_analysis)에서
    해당 종목코드의 최신 분석 리포트(핵심 답변 및 DART 공시)를 수집.
    게시글이 없는 종목일 경우 None 반환 (오류 아님).
    """
    search_url = f"{EZINIT_ANALYSIS_URL}?search={code}"
    logger.info(f"[EZINIT] 우쎔AI 종목분석 검색 시작 (code={code})")

    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"[EZINIT] 검색 페이지 접근 실패 ({resp.status_code})")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        target_href = None
        post_title = ""

        for a in soup.find_all("a", href=True):
            href = a["href"]
            txt = a.get_text(" ", strip=True)
            if "/community/stock_analysis/" in href and f"({code})" in txt:
                target_href = href
                post_title = txt
                break

        if not target_href:
            logger.info(f"[EZINIT] '{code}' 종목 분석 게시글 없음")
            return None

        detail_url = (
            target_href if target_href.startswith("http")
            else f"https://portfolio.ezinit.com{target_href}"
        )
        detail_resp = requests.get(detail_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if detail_resp.status_code != 200:
            logger.warning(f"[EZINIT] 상세 리포트 접근 실패 ({detail_resp.status_code})")
            return None

        dsoup = BeautifulSoup(detail_resp.text, "html.parser")
        for tag in dsoup.select("nav, header, footer, script, style"):
            tag.decompose()

        content_el = dsoup.select_one(".post-content, .article-content, .content, article, main")
        full_text = content_el.get_text("\n", strip=True) if content_el else dsoup.get_text("\n", strip=True)

        # 핵심 답변 섹션 추출
        summary_text = ""
        m = re.search(r"📊\s*핵심\s*답변\s*(.*?)(?=📌\s*핵심\s*지표|📑\s*최근\s*공시|$)", full_text, re.DOTALL)
        if m:
            summary_text = m.group(1).strip()

        # 최근 공시(DART) 항목 추출 (최대 5건)
        disclosures = []
        for tr in dsoup.select("table tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}", tds[0]):
                disclosures.append({
                    "date": tds[0],
                    "category": tds[1],
                    "title": tds[2].replace("↗", "").strip(),
                })

        logger.info(f"[EZINIT] 우쎔AI 리포트 수집 완료 (title={post_title}, 공시={len(disclosures)}건)")
        return {
            "title": post_title,
            "url": detail_url,
            "summary": summary_text,
            "disclosures": disclosures[:5],
        }

    except Exception as e:
        logger.warning(f"[EZINIT] 수집 중 예외 발생 (무시하고 계속 진행): {e}")
        return None


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


def resolve_stock_code(query):
    """
    사용자가 입력한 6자리 종목코드 또는 한글 종목명(예: 'SK하이닉스', 'KODEX 200')을
    국내 6자리 종목코드로 변환한다.
    반환: (code 또는 None, 오류 메시지 또는 None)
    """
    q = (query or "").strip()
    if not q:
        return None, "종목코드 또는 종목명을 입력해 주세요."

    # 이미 6자리 국내 코드(숫자 6자리 또는 영문+숫자 6자리 ETF 코드)인 경우 그대로 반환
    if re.fullmatch(r"\d{6}|[0-9A-Za-z]{6}", q) and any(ch.isdigit() for ch in q):
        return q.upper(), None

    logger.info(f"[NAVER] 종목명 -> 종목코드 자동 검색 시작 (query={q})")
    try:
        resp = requests.get(
            NAVER_AC_URL,
            params={"q": q, "target": "stock"},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None, f"종목명 검색 API 호출에 실패했습니다 ({resp.status_code})."

        items = resp.json().get("items", [])
        kor_items = [
            it for it in items
            if it.get("nationCode") == "KOR" and it.get("code")
        ]
        if not kor_items:
            return None, f"'{q}'에 해당하는 국내 종목을 찾을 수 없습니다. 정확한 종목명이나 6자리 코드를 입력해 주세요."

        # 정확히 이름이 일치하는 항목 최우선 선택
        q_norm = q.replace(" ", "").upper()
        for it in kor_items:
            if (it.get("name") or "").replace(" ", "").upper() == q_norm:
                logger.info(f"[NAVER] 종목코드 매핑 완료: {q} -> {it['code']} ({it.get('name')})")
                return it["code"], None

        first = kor_items[0]
        logger.info(f"[NAVER] 종목코드 매핑 완료(최상단): {q} -> {first['code']} ({first.get('name')})")
        return first["code"], None

    except Exception as e:
        logger.error(f"[ERROR] 종목명 검색 중 오류 발생 (query={q})", exc_info=True)
        return None, f"종목명 검색 중 오류가 발생했습니다: {e}"


def _get_dart_corp_map(dart_key):
    """OpenDART 전체 고유번호(CORPCODE.xml)를 1회 다운로드하여 stock_code -> corp_code 매핑 캐싱"""
    global _DART_CORP_CACHE
    if _DART_CORP_CACHE:
        return _DART_CORP_CACHE

    logger.info("[DART] OpenDART 고유번호(corpCode) 매핑표 다운로드 시작")
    try:
        resp = requests.get(DART_CORP_CODE_URL, params={"crtfc_key": dart_key}, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[DART] corpCode.xml 다운로드 실패 ({resp.status_code})")
            return {}

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            with zf.open(xml_name) as f:
                tree = ET.parse(f)
                root = tree.getroot()
                mapping = {}
                for el in root.findall("list"):
                    stock_cd = (el.findtext("stock_code") or "").strip()
                    corp_cd = (el.findtext("corp_code") or "").strip()
                    if stock_cd and corp_cd:
                        mapping[stock_cd] = corp_cd
                _DART_CORP_CACHE = mapping
                logger.info(f"[DART] 고유번호 매핑 캐시 완료 (상장사 {len(mapping)}개)")
                return _DART_CORP_CACHE
    except Exception as e:
        logger.warning(f"[DART] 고유번호 매핑 실패: {e}")
        return {}


def fetch_dart_disclosures(code):
    """
    금융감독원 OpenDART API(/api/list.json)에서 최근 90일 공식 공시 최대 8건을 조회한다.
    DART_API_KEY 미설정 또는 비상장/ETF 종목인 경우 빈 리스트 반환.
    """
    dart_key = os.getenv("DART_API_KEY", "").strip()
    if not dart_key:
        return []

    corp_map = _get_dart_corp_map(dart_key)
    corp_code = corp_map.get(code)
    if not corp_code:
        logger.info(f"[DART] '{code}'에 해당하는 DART 상장사 고유번호가 없습니다 (ETF 등).")
        return []

    bgn_de = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    logger.info(f"[DART] OpenDART 공시 조회 시작 (code={code}, corp_code={corp_code})")

    try:
        resp = requests.get(
            DART_LIST_URL,
            params={
                "crtfc_key": dart_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "page_count": 8,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(f"[DART] 공시 목록 API 비정상 응답 ({resp.status_code})")
            return []

        data = resp.json()
        if data.get("status") != "000":
            logger.info(f"[DART] 조회 결과: {data.get('message')} (status={data.get('status')})")
            return []

        results = []
        for item in (data.get("list") or [])[:8]:
            raw_dt = item.get("rcept_dt", "")
            fmt_dt = f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:]}" if len(raw_dt) == 8 else raw_dt
            rcp_no = item.get("rcept_no", "")
            results.append({
                "date": fmt_dt,
                "title": (item.get("report_nm") or "").strip(),
                "submitter": (item.get("flr_nm") or "").strip(),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}" if rcp_no else "",
            })

        logger.info(f"[DART] 공식 공시 수집 완료 (code={code}, 건수={len(results)})")
        return results

    except Exception as e:
        logger.warning(f"[DART] 공시 조회 중 예외 발생: {e}")
        return []


def collect_stock_data(code_or_name):
    """종목코드 또는 종목명을 검증/변환하고 전체 주식 데이터를 병렬 수집하여 규격 JSON(dict) 반환"""
    errors = []
    clean_code, resolve_err = resolve_stock_code(code_or_name)

    if resolve_err or not clean_code:
        errors.append(resolve_err or "종목코드를 확인할 수 없습니다.")
        return {
            "code": (code_or_name or "").strip(),
            "name": None,
            "snapshot": {},
            "financials": [],
            "news": [],
            "errors": errors,
        }

    # 1단계: 기본 시세 + 정식 종목명 확보 (뉴스 검색어 확보를 위해 선행)
    name, snapshot, extra_naver = fetch_basic_quote(clean_code, errors)

    # 2단계: 연간실적, 분기실적, 시장지수, 우쎔AI, OpenDART공시, Serper뉴스 6개 작업 동시 병렬 수집
    annual_errors, quarter_errors, news_errors = [], [], []
    with ThreadPoolExecutor(max_workers=6) as executor:
        fut_annual = executor.submit(fetch_financials, clean_code, annual_errors, "annual")
        fut_quarter = executor.submit(fetch_financials, clean_code, quarter_errors, "quarter")
        fut_market = executor.submit(fetch_market_indices)
        fut_ezinit = executor.submit(fetch_ezinit_analysis, clean_code)
        fut_dart = executor.submit(fetch_dart_disclosures, clean_code)
        fut_news = executor.submit(fetch_news, name, news_errors)

        financials = fut_annual.result()
        quarter_financials = fut_quarter.result()
        market_indices = fut_market.result()
        ezinit_analysis = fut_ezinit.result()
        dart_disclosures = fut_dart.result()
        news = fut_news.result()

    errors.extend(annual_errors)
    errors.extend(quarter_errors)
    errors.extend(news_errors)

    return {
        "code": clean_code,
        "name": name,
        "snapshot": snapshot,
        "market_indices": market_indices,
        "consensus": extra_naver.get("consensus", {}),
        "investor_trends": extra_naver.get("investor_trends", []),
        "researches": extra_naver.get("researches", []),
        "peers": extra_naver.get("peers", []),
        "financials": financials,
        "quarter_financials": quarter_financials,
        "ezinit_analysis": ezinit_analysis,
        "dart_disclosures": dart_disclosures,
        "news": news,
        "errors": errors,
    }
