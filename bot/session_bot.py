import argparse
from dataclasses import dataclass

from bot.market_data import fetch_live_candles
from bot.signal_engine import analyze_setup
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

SESSION_WATCHLISTS = {
    "london": [
        "EUR_USD",
        "GBP_USD",
        "USD_JPY",
        "XAU_USD",
        "BTC_USD",
        "ETH_USD",
    ],
    "new_york": [
        "US30",
        "USD_TRY",
        "GBP_JPY",
        "GBP_NZD",
        "USD_ZAR",
    ],
}

ALL_WATCHLIST = SESSION_WATCHLISTS["london"] + SESSION_WATCHLISTS["new_york"]


def scan_symbol(symbol: str, min_confidence: int, session_key: str | None = None) -> str:
    try:
        prices = fetch_live_candles(symbol)
    except Exception as exc:
        return f"{symbol}: Data unavailable ({exc})"

    report = analyze_setup(
        symbol,
        prices,
        min_confidence=min_confidence,
        session_key=session_key,
    )
    return report.to_message()


def build_session_message(session_key: str, min_confidence: int) -> str:
    session = SESSIONS[session_key]
    watchlist = SESSION_WATCHLISTS.get(session_key, ALL_WATCHLIST)

    lines = [
        f"{session.name} Open",
        f"Focus: {session.focus}",
        "",
        "Market scan:",
    ]

    for symbol in watchlist:
        lines.append(scan_symbol(symbol, min_confidence, session_key=session_key))
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

    for symbol in ALL_WATCHLIST:
        lines.append(scan_symbol(symbol, min_confidence, session_key=None))
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
