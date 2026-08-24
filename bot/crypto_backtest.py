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
        prices = fetch_live_candles(symbol, interval="15min", outputsize=200)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    if len(prices) < 90:
        return {"symbol": symbol, "error": "Not enough candles"}

    trades = []
    in_trade = False
    current_trade = None

    # Walk-forward simulation over sliding windows
    for i in range(80, len(prices) - 5):
        window = prices.iloc[: i + 1].copy()
        current_candle = prices.iloc[i]

        if in_trade:
            # Check outcome against forward prices
            high = current_candle["high"]
            low = current_candle["low"]
            side = current_trade["action"]
            sl = current_trade["sl"]
            tp = current_trade["tp"]

            if side == "BUY":
                if low <= sl:
                    trades.append({"symbol": symbol, "result": "LOSS", "r": -1.0, "reason": "Hit SL", "entry": current_trade["entry"], "sl": sl, "tp": tp, "exit_price": low})
                    print(f"[{symbol}] LOSS BUY Entry: {current_trade['entry']:.4f}, SL: {sl:.4f}, Low: {low:.4f}")
                    in_trade = False
                    current_trade = None
                elif high >= tp:
                    trades.append({"symbol": symbol, "result": "WIN", "r": 1.67, "reason": "Hit TP", "entry": current_trade["entry"], "sl": sl, "tp": tp, "exit_price": high})
                    print(f"[{symbol}] WIN BUY Entry: {current_trade['entry']:.4f}, TP: {tp:.4f}, High: {high:.4f}")
                    in_trade = False
                    current_trade = None
            elif side == "SELL":
                if high >= sl:
                    trades.append({"symbol": symbol, "result": "LOSS", "r": -1.0, "reason": "Hit SL", "entry": current_trade["entry"], "sl": sl, "tp": tp, "exit_price": high})
                    print(f"[{symbol}] LOSS SELL Entry: {current_trade['entry']:.4f}, SL: {sl:.4f}, High: {high:.4f}")
                    in_trade = False
                    current_trade = None
                elif low <= tp:
                    trades.append({"symbol": symbol, "result": "WIN", "r": 1.67, "reason": "Hit TP", "entry": current_trade["entry"], "sl": sl, "tp": tp, "exit_price": low})
                    print(f"[{symbol}] WIN SELL Entry: {current_trade['entry']:.4f}, TP: {tp:.4f}, Low: {low:.4f}")
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
                "tp": report.take_profit,
                "confidence": report.confidence,
                "strategy": report.reason,
            }
            print(f"[{symbol}] NEW {report.action} at candle {i}: Entry={report.entry:.4f}, SL={report.stop_loss:.4f}, TP={report.take_profit:.4f}, Reason={report.reason}")

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total = len(trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0.0
    net_r = sum(t["r"] for t in trades)

    return {
        "symbol": DISPLAY_NAMES.get(symbol, symbol),
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_r": net_r,
    }


def run_crypto_profitability_check() -> None:
    print("=" * 60)
    print("CRYPTO SIGNAL PROFITABILITY & ACCURACY BACKTEST")
    print("=" * 60)

    results = []
    for sym in CRYPTO_PAIRS:
        res = backtest_symbol(sym, min_confidence=60)
        results.append(res)
        time.sleep(8)  # Throttling for 8 req/min API limit

    print(f"{'Symbol':<10} | {'Trades':<8} | {'Wins':<6} | {'Losses':<6} | {'Win Rate':<10} | {'Net Return (R)':<12}")
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
        print(f"{r['symbol']:<10} | {r['total_trades']:<8} | {r['wins']:<6} | {r['losses']:<6} | {r['win_rate']:<9.1f}% | {r['net_r']:>+10.1f}R")

    overall_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    print("=" * 65)
    print(f"OVERALL SUMMARY: {tot_trades} Trades | {tot_wins} Wins | Win Rate: {overall_wr:.1f}% | Total Net Profit: {tot_r:+.1f}R")
    print("=" * 65)


if __name__ == "__main__":
    run_crypto_profitability_check()
