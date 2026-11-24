import os
import time
import pandas as pd
import requests
from dotenv import load_dotenv

from bot.market_data import fetch_live_candles
from bot.signal_engine import analyze_setup
from bot.symbols import DISPLAY_NAMES

CRYPTO_PAIRS = ["BTC_USD", "ETH_USD", "SOL_USD", "XRP_USD", "DOGE_USD", "ADA_USD"]


def backtest_symbol(symbol: str, min_confidence: int = 60) -> dict:
    try:
        # Use the largest recent candle window supported by the data providers.
        prices = fetch_live_candles(symbol, interval="15min", outputsize=1000)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    if len(prices) < 90:
        return {"symbol": symbol, "error": "Not enough candles"}

    trades = []
    in_trade = False
    current_trade = None

    # Historical signal evaluation must ignore current news, futures positioning,
    # and BTC veto values; those are not available for the old candle timestamps.
    old_backtest_mode = os.environ.get("BACKTEST_MODE")
    os.environ["BACKTEST_MODE"] = "1"
    try:
        # Walk-forward simulation over sliding windows. If one candle touches
        # both SL and TP, count it as an SL (conservative OHLC-only assumption).
        for i in range(80, len(prices) - 5):
            window = prices.iloc[: i + 1].copy()
            current_candle = prices.iloc[i]

            if in_trade:
                high = float(current_candle["high"])
                low = float(current_candle["low"])
                side = current_trade["action"]
                sl = current_trade["sl"]
                tp = current_trade["tp"]

                if side == "BUY":
                    stop_hit, target_hit = low <= sl, high >= tp
                else:
                    stop_hit, target_hit = high >= sl, low <= tp

                if stop_hit or target_hit:
                    # A simultaneous touch is resolved in the stop's favor.
                    won = target_hit and not stop_hit
                    exit_price = tp if won else sl
                    risk = abs(current_trade["entry"] - sl)
                    reward = abs(tp - current_trade["entry"])
                    r_multiple = reward / risk if won and risk > 0 else -1.0
                    trades.append({
                        "symbol": symbol,
                        "result": "WIN" if won else "LOSS",
                        "r": r_multiple,
                        "reason": "Hit TP" if won else "Hit SL",
                        "entry": current_trade["entry"],
                        "sl": sl,
                        "tp": tp,
                        "exit_price": exit_price,
                        "confidence": current_trade["confidence"],
                        "strategy": current_trade["strategy"],
                    })
                    in_trade = False
                    current_trade = None
                continue

            report = analyze_setup(symbol, window, min_confidence=min_confidence)
            if report.action in ("BUY", "SELL") and report.entry and report.stop_loss and report.take_profit:
                in_trade = True
                current_trade = {
                    "action": report.action,
                    "entry": report.entry,
                    "sl": report.stop_loss,
                    "tp": report.tp1 or report.take_profit,
                    "confidence": report.confidence,
                    "strategy": report.reason,
                }
    finally:
        if old_backtest_mode is None:
            os.environ.pop("BACKTEST_MODE", None)
        else:
            os.environ["BACKTEST_MODE"] = old_backtest_mode

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total = len(trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0.0
    net_r = sum(t["r"] for t in trades)

    return {
        "symbol": DISPLAY_NAMES.get(symbol, symbol),
        "candles": len(prices),
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_r": net_r,
        "open_trade_at_end": in_trade,
        "costs_included": False,
    }


def run_crypto_profitability_check() -> None:
    print("=" * 60)
    print("CRYPTO SIGNAL WALK-FORWARD SCREEN (GROSS, BEFORE COSTS)")
    print("=" * 60)

    results = []
    for sym in CRYPTO_PAIRS:
        res = backtest_symbol(sym, min_confidence=60)
        results.append(res)
        time.sleep(8)  # Throttling for 8 req/min API limit

    print(f"{'Symbol':<10} | {'Bars':<6} | {'Trades':<8} | {'Wins':<6} | {'Losses':<6} | {'Win Rate':<10} | {'Net Return (R)':<12}")
    print("-" * 65)

    tot_trades = 0
    tot_wins = 0
    tot_r = 0.0

    for r in results:
        if "error" in r:
            print(f"{r['symbol']:<10} | ERROR: {r['error']}")
            continue
        tot_trades += r["total_trades"]
        tot_wins += r["wins"]
        tot_r += r["net_r"]
        print(f"{r['symbol']:<10} | {r['candles']:<6} | {r['total_trades']:<8} | {r['wins']:<6} | {r['losses']:<6} | {r['win_rate']:<9.1f}% | {r['net_r']:>+10.1f}R")

    overall_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    print("=" * 65)
    print(f"OVERALL SUMMARY: {tot_trades} Closed Trades | {tot_wins} Wins | Win Rate: {overall_wr:.1f}% | Gross Return: {tot_r:+.1f}R")
    print("Results use at most 1,000 recent 15-minute candles and exclude fees, slippage, and funding.")
    print("=" * 65)


if __name__ == "__main__":
    run_crypto_profitability_check()

