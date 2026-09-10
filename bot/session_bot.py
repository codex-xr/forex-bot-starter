import argparse
from dataclasses import dataclass
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.market_data import fetch_live_candles
from bot.signal_engine import analyze_setup
from bot.telegram import send_telegram_message


@dataclass(frozen=True)
class MarketSession:
    name: str
    focus: str


SESSIONS = {
    "f1": MarketSession("Forex Batch 1 (/f1)", "Majors (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD)"),
    "f2": MarketSession("Forex Batch 2 (/f2)", "Crosses & Commodities (NZDUSD, EURGBP, EURJPY, GBPJPY, Gold, US30)"),
    "c1": MarketSession("Major Cryptos (/c1)", "BTC, ETH, SOL, XRP, DOGE, ADA"),
    "c2": MarketSession("High-Momentum Altcoins (/c2)", "BNB, AVAX, LINK, SUI, NEAR, LTC"),
    "m1": MarketSession("Top Memecoins (/m1)", "WIF, PEPE, SHIB, BONK, FLOKI, BRETT, ANSEM"),
    "m2": MarketSession("Trending & Narrative Memes (/m2)", "TRUMP, BOME, PENGU, MOG, PEOPLE, ELON"),
}

SESSION_WATCHLISTS = {
    "f1": [
        "EUR_USD",
        "GBP_USD",
        "USD_JPY",
        "USD_CHF",
        "AUD_USD",
        "USD_CAD",
    ],
    "f2": [
        "NZD_USD",
        "EUR_GBP",
        "EUR_JPY",
        "GBP_JPY",
        "XAU_USD",
        "US30",
    ],
    "c1": [
        "BTC_USD",
        "ETH_USD",
        "SOL_USD",
        "XRP_USD",
        "DOGE_USD",
        "ADA_USD",
    ],
    "c2": [
        "BNB_USD",
        "AVAX_USD",
        "LINK_USD",
        "SUI_USD",
        "NEAR_USD",
        "LTC_USD",
    ],
    "m1": [
        "WIF_USD",
        "PEPE_USD",
        "SHIB_USD",
        "BONK_USD",
        "FLOKI_USD",
        "BRETT_USD",
        "ANSEM_USD",
    ],
    "m2": [
        "TRUMP_USD",
        "BOME_USD",
        "PENGU_USD",
        "MOG_USD",
        "PEOPLE_USD",
        "ELON_USD",
    ],
}

ALL_WATCHLIST = list(dict.fromkeys(
    sym for watchlist in SESSION_WATCHLISTS.values() for sym in watchlist
))


def scan_symbol_report(symbol: str, min_confidence: int, session_key: str | None = None) -> tuple[str, object | None]:
    try:
        prices = fetch_live_candles(symbol)
    except Exception as exc:
        return f"{symbol}: Data unavailable ({exc})", None

    report = analyze_setup(
        symbol,
        prices,
        min_confidence=min_confidence,
        session_key=session_key,
    )
    return report.to_message(), report


import concurrent.futures


def build_session_scan(session_key: str, min_confidence: int) -> tuple[str, list]:
    session = SESSIONS[session_key]
    watchlist = SESSION_WATCHLISTS.get(session_key, ALL_WATCHLIST)

    lines = [
        f"📊 <b>{session.name}</b>",
        f"<i>Focus: {session.focus}</i>",
        "",
        "<b>Market Scan:</b>",
        "",
    ]

    # Fetch and analyze symbols concurrently in parallel (sub-second response)
    sym_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(watchlist))) as executor:
        future_to_sym = {
            executor.submit(scan_symbol_report, sym, min_confidence, session_key): sym
            for sym in watchlist
        }
        for future in concurrent.futures.as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                sym_results[sym] = future.result()
            except Exception as e:
                sym_results[sym] = (f"{sym}: Data unavailable ({e})", None)

    setups = []
    for symbol in watchlist:
        msg, report = sym_results.get(symbol, (f"{symbol}: Data unavailable", None))
        lines.append(msg)
        lines.append("")
        if report and getattr(report, "action", "") in ("BUY", "SELL"):
            setups.append(report)

    return "\n".join(lines).strip(), setups


def build_session_message(session_key: str, min_confidence: int) -> str:
    msg, _ = build_session_scan(session_key, min_confidence)
    return msg


def run_session(session_key: str, min_confidence: int) -> None:
    message = build_session_message(session_key, min_confidence)
    print(message)
    send_telegram_message(message)


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