import logging
from datetime import datetime, timedelta
import FinanceDataReader as fdr
import numpy as np
import pandas as pd
from collector import resolve_stock_code

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


def fetch_price_history(ticker, days=365):
    """
    FinanceDataReader를 사용해 특정 종목/ETF의 최근 N일 일봉 데이터를 조회한다.
    반환: (DataFrame 또는 None, 오류 메시지 또는 None)
    """
    clean_ticker = (ticker or "").strip()
    if not clean_ticker:
        return None, "종목코드가 비어 있습니다."

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    logger.info(f"[FDR] FinanceDataReader 주가 조회 시작 (ticker={clean_ticker}, {start_str} ~ {end_str})")

    try:
        df = fdr.DataReader(clean_ticker, start_str, end_str)
        if df is None or df.empty or "Close" not in df.columns:
            msg = f"'{clean_ticker}' 종목의 주가 데이터를 찾을 수 없습니다. 코드를 확인해 주세요."
            logger.warning(f"[ERROR] {msg}")
            return None, msg

        df = df.dropna(subset=["Close"]).copy()
        if len(df) < 20:
            msg = f"'{clean_ticker}' 종목의 최근 거래일 데이터가 부족합니다 ({len(df)}일)."
            logger.warning(f"[ERROR] {msg}")
            return None, msg

        logger.info(f"[FDR] 주가 조회 완료 (ticker={clean_ticker}, 거래일수={len(df)})")
        return df, None

    except Exception as e:
        msg = f"'{clean_ticker}' 주가 데이터 조회 중 오류가 발생했습니다: {e}"
        logger.error(f"[ERROR] FDR 조회 실패 (ticker={clean_ticker})", exc_info=True)
        return None, msg


def calc_basic_metrics(close_series, risk_free_rate=0.0):
    """
    종가 시계열(pd.Series)로부터 연율화 수익률(%), 변동성(%), 샤프지수, MDD(%) 계산
    """
    returns = close_series.pct_change().dropna()
    if returns.empty:
        return {
            "annual_return": 0.0,
            "volatility": 0.0,
            "sharpe_ratio": 0.0,
            "mdd": 0.0,
        }

    annual_return = float(returns.mean() * TRADING_DAYS_PER_YEAR * 100)
    volatility = float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)
    sharpe = float((annual_return - risk_free_rate) / volatility) if volatility > 0 else 0.0

    # MDD (Maximum Drawdown) 계산
    cum_wealth = (1 + returns).cumprod()
    running_peak = cum_wealth.cummax()
    drawdown = (cum_wealth - running_peak) / running_peak
    mdd = float(drawdown.min() * 100)

    return {
        "annual_return": round(annual_return, 2),
        "volatility": round(volatility, 2),
        "sharpe_ratio": round(sharpe, 2),
        "mdd": round(mdd, 2),
    }


def calculate_rebalancing(tickers, num_simulations=3000):
    """
    [탭 2] 보유 종목/ETF 리스트를 받아 최근 1년 데이터 기반
    단순화 몬테카를로 시뮬레이션(3,000회) 및 효율적 프론티어 최적 비중 계산
    """
    errors = []
    # 종목명 -> 코드 변환 및 중복 제거
    clean_tickers = []
    for t in tickers or []:
        raw_item = str(t).strip()
        if not raw_item:
            continue
        resolved_code, r_err = resolve_stock_code(raw_item)
        if r_err or not resolved_code:
            errors.append(r_err or f"'{raw_item}' 코드를 찾을 수 없습니다.")
            continue
        if resolved_code not in clean_tickers:
            clean_tickers.append(resolved_code)

    if len(clean_tickers) < 2:
        errors.append("리밸런싱을 계산하려면 서로 다른 종목/ETF 코드를 2개 이상 입력해야 합니다.")
        return {
            "tickers": clean_tickers,
            "period_days": 0,
            "optimal_portfolio": None,
            "asset_stats": {},
            "frontier_points": [],
            "errors": errors,
        }

    close_series_map = {}
    asset_stats = {}

    for code in clean_tickers:
        df, err = fetch_price_history(code, days=365)
        if err:
            errors.append(err)
            continue
        close_series_map[code] = df["Close"]
        stats = calc_basic_metrics(df["Close"])
        stats["latest_price"] = round(float(df["Close"].iloc[-1]), 0)
        asset_stats[code] = stats

    valid_tickers = list(close_series_map.keys())
    if len(valid_tickers) < 2:
        errors.append("유효한 주가 데이터를 조회한 종목이 2개 미만이어서 리밸런싱을 계산할 수 없습니다.")
        return {
            "tickers": valid_tickers,
            "period_days": 0,
            "optimal_portfolio": None,
            "asset_stats": asset_stats,
            "frontier_points": [],
            "errors": errors,
        }

    # 공통 거래일 기준 병합
    prices_df = pd.concat(close_series_map, axis=1).dropna()
    if len(prices_df) < 20:
        errors.append("선택한 종목들의 공통 거래일 데이터가 20일 미만으로 부족합니다.")
        return {
            "tickers": valid_tickers,
            "period_days": len(prices_df),
            "optimal_portfolio": None,
            "asset_stats": asset_stats,
            "frontier_points": [],
            "errors": errors,
        }

    returns_df = prices_df.pct_change().dropna()
    mean_daily_ret = returns_df.mean().values
    cov_matrix = returns_df.cov().values
    n_assets = len(valid_tickers)

    # 몬테카를로 시뮬레이션 (벡터화 대신 명확한 루프로 초보자 가독성 및 안정성 확보)
    all_weights = np.zeros((num_simulations, n_assets))
    sim_returns = np.zeros(num_simulations)
    sim_vols = np.zeros(num_simulations)
    sim_sharpes = np.zeros(num_simulations)

    for i in range(num_simulations):
        w = np.random.random(n_assets)
        w /= np.sum(w)
        all_weights[i] = w

        port_ret = float(np.sum(mean_daily_ret * w) * TRADING_DAYS_PER_YEAR * 100)
        port_vol = float(np.sqrt(np.dot(w.T, np.dot(cov_matrix * TRADING_DAYS_PER_YEAR, w))) * 100)
        sharpe = port_ret / port_vol if port_vol > 0 else 0.0

        sim_returns[i] = port_ret
        sim_vols[i] = port_vol
        sim_sharpes[i] = sharpe

    # 최대 샤프지수 포트폴리오 선정
    best_idx = int(np.argmax(sim_sharpes))
    best_weights = all_weights[best_idx]

    # 최적 포트폴리오의 과거 1년 MDD 계산
    port_daily_ret = (returns_df * best_weights).sum(axis=1)
    cum_wealth = (1 + port_daily_ret).cumprod()
    running_peak = cum_wealth.cummax()
    port_mdd = float(((cum_wealth - running_peak) / running_peak).min() * 100)

    optimal_portfolio = {
        "weights": {
            ticker: round(float(best_weights[idx]), 4)
            for idx, ticker in enumerate(valid_tickers)
        },
        "expected_return": round(float(sim_returns[best_idx]), 2),
        "volatility": round(float(sim_vols[best_idx]), 2),
        "sharpe_ratio": round(float(sim_sharpes[best_idx]), 2),
        "mdd": round(port_mdd, 2),
    }

    # 차트 시각화용 효율적 프론티어 포인트 샘플링 (최대 250개)
    sample_step = max(1, num_simulations // 250)
    frontier_points = [
        {
            "return": round(float(sim_returns[i]), 2),
            "volatility": round(float(sim_vols[i]), 2),
            "sharpe": round(float(sim_sharpes[i]), 2),
        }
        for i in range(0, num_simulations, sample_step)
    ]

    logger.info(
        f"[QUANT] 몬테카를로 리밸런싱 완료 (tickers={valid_tickers}, "
        f"simulations={num_simulations}, best_sharpe={optimal_portfolio['sharpe_ratio']})"
    )

    return {
        "tickers": valid_tickers,
        "period_days": len(prices_df),
        "optimal_portfolio": optimal_portfolio,
        "asset_stats": asset_stats,
        "frontier_points": frontier_points,
        "errors": errors,
    }


def _series_to_clean_list(series, decimals=2):
    """Pandas Series의 NaN을 None으로 치환하여 JSON 직렬화 가능한 리스트로 반환"""
    result = []
    for val in series:
        if pd.isna(val):
            result.append(None)
        else:
            result.append(round(float(val), decimals))
    return result


def calculate_technical_indicators(code, days=365, chart_days=120):
    """
    [탭 3] FinanceDataReader 주가 데이터로 MA(5/20/60), RSI(14),
    MACD(12,26,9), 볼린저밴드(20,2)를 계산하여 차트용 배열과 최신 요약 숫자를 반환
    """
    clean_code, resolve_err = resolve_stock_code(code)
    if resolve_err or not clean_code:
        return {
            "code": (code or "").strip(),
            "dates": [],
            "close": [],
            "indicators": {},
            "latest_summary": {},
            "errors": [resolve_err or "종목코드를 확인할 수 없습니다."],
        }
    df, err = fetch_price_history(clean_code, days=days)
    if err:
        return {
            "code": clean_code,
            "dates": [],
            "close": [],
            "indicators": {},
            "latest_summary": {},
            "errors": [err],
        }

    close = df["Close"].astype(float)

    # 1. 이동평균선 (MA 5 / 20 / 60)
    ma5 = close.rolling(window=5).mean()
    ma20 = close.rolling(window=20).mean()
    ma60 = close.rolling(window=60).mean()

    # 2. RSI (14일, Wilder EMA 방식)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14 = 100 - (100 / (1 + rs))

    # 3. MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    # 4. 볼린저밴드 (20일, 2표준편차)
    bb_std = close.rolling(window=20).std()
    bb_upper = ma20 + (2 * bb_std)
    bb_lower = ma20 - (2 * bb_std)

    # 차트 시각화용 최근 chart_days 거래일 슬라이싱
    dates_all = [idx.strftime("%Y-%m-%d") for idx in df.index]
    slice_start = max(0, len(dates_all) - chart_days)

    dates = dates_all[slice_start:]
    close_list = _series_to_clean_list(close.iloc[slice_start:], decimals=0)
    ma5_list = _series_to_clean_list(ma5.iloc[slice_start:], decimals=0)
    ma20_list = _series_to_clean_list(ma20.iloc[slice_start:], decimals=0)
    ma60_list = _series_to_clean_list(ma60.iloc[slice_start:], decimals=0)
    rsi14_list = _series_to_clean_list(rsi14.iloc[slice_start:], decimals=2)
    macd_list = _series_to_clean_list(macd.iloc[slice_start:], decimals=2)
    macd_sig_list = _series_to_clean_list(macd_signal.iloc[slice_start:], decimals=2)
    bb_up_list = _series_to_clean_list(bb_upper.iloc[slice_start:], decimals=0)
    bb_low_list = _series_to_clean_list(bb_lower.iloc[slice_start:], decimals=0)

    latest_summary = {
        "date": dates[-1] if dates else None,
        "close": close_list[-1] if close_list else None,
        "ma5": ma5_list[-1] if ma5_list else None,
        "ma20": ma20_list[-1] if ma20_list else None,
        "ma60": ma60_list[-1] if ma60_list else None,
        "rsi14": rsi14_list[-1] if rsi14_list else None,
        "macd": macd_list[-1] if macd_list else None,
        "macd_signal": macd_sig_list[-1] if macd_sig_list else None,
        "bb_upper": bb_up_list[-1] if bb_up_list else None,
        "bb_lower": bb_low_list[-1] if bb_low_list else None,
    }

    logger.info(
        f"[QUANT] 기술지표 계산 완료 (code={clean_code}, chart_days={len(dates)}, "
        f"rsi14={latest_summary['rsi14']})"
    )

    return {
        "code": clean_code,
        "dates": dates,
        "close": close_list,
        "indicators": {
            "ma5": ma5_list,
            "ma20": ma20_list,
            "ma60": ma60_list,
            "rsi14": rsi14_list,
            "macd": macd_list,
            "macd_signal": macd_sig_list,
            "bb_upper": bb_up_list,
            "bb_lower": bb_low_list,
        },
        "latest_summary": latest_summary,
        "errors": [],
    }
