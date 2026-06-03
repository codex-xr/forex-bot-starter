import argparse
from dataclasses import dataclass
from pathlib import Path

from bot.market_data import fetch_live_candles
from bot.strategy import MovingAverageCrossover, Signal
from bot.telegram import send_telegram_message


@dataclass(frozen=True)
class MarketSession:
    name: str
    focus: str


SESSIONS = {
    "tokyo": MarketSession("Tokyo Session", "JPY pairs, AUD, NZD"),
    "london": MarketSession("London Session", "EUR, GBP, gold, major forex pairs"),
    "new_york": MarketSession("New York Session", "USD pairs, gold, oil, indices"),
    "overlap": MarketSession("London/New York Overlap", "Highest liquidity window"),
}

WATCHLIST = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "XAU_USD",
]


def symbol_data_path(symbol: str) -> Path:
    return Path("data") / f"{symbol.lower()}.csv"


def confidence_score(prices, signal: str) -> int:
    closes = prices["close"]
    recent_change = abs((closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5])

    if signal == Signal.HOLD.value:
        return 0

    score = 60

    if recent_change > 0.002:
        score += 10
    if recent_change > 0.005:
        score += 10

    return min(score, 85)


def scan_symbol(symbol: str, min_confidence: int) -> str:
    try:
        prices = fetch_live_candles(symbol)
    except Exception as exc:
        return f"{symbol}: Data unavailable ({exc})" 
    strategy = MovingAverageCrossover(fast_window=5, slow_window=20)
    signals = strategy.generate(prices)

    signal = signals.iloc[-1]
    confidence = confidence_score(prices, signal)

    if signal == Signal.HOLD.value or confidence < min_confidence:
        return f"{symbol}: No clean setup"

    last_price = float(prices['close'].iloc[-1])

    return (
        f"{symbol}: {signal.upper()} setup\n"
        f"Confidence: {confidence}%\n"
        f"Price: {last_price:.5f}\n"
        f"Reason: Moving-average signal with recent momentum confirmation"
    )


def build_session_message(session_key: str, min_confidence: int) -> str:
    session = SESSIONS[session_key]

    lines = [
        f"{session.name} Open",
        f"Focus: {session.focus}",
        "",
        "Market scan:",
    ]

    for symbol in WATCHLIST:
        lines.append(scan_symbol(symbol, min_confidence))
        lines.append("")

    return "\n".join(lines).strip()


def run_session(session_key: str, min_confidence: int) -> None:
    message = build_session_message(session_key, min_confidence)
    print(message)
    send_telegram_message(message)

def build_all_sessions_message(min_confidence: int) -> str:
    lines = [
        "All Market Sessions Signal Scan",
        "",
        "Sessions:",
    ]

    for session in SESSIONS.values():
        lines.append(f"- {session.name}: {session.focus}")

    lines.extend(["", "Market scan:"])

    for symbol in WATCHLIST:
        lines.append(scan_symbol(symbol, min_confidence))
        lines.append("")

    return "\n".join(lines).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram forex session scanner")
    parser.add_argument(
        "--session",
        choices=SESSIONS.keys(),
        required=True,
    )
    parser.add_argument("--min-confidence", type=int, default=70)
    args = parser.parse_args()

    run_session(args.session, args.min_confidence)


if __name__ == "__main__":
    main()
