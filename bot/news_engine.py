from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
import xml.etree.ElementTree as ET


BULLISH_KEYWORDS = {
    "strategic reserve": 35,
    "bitcoin reserve": 35,
    "crypto reserve": 30,
    "executive order": 25,
    "approval": 25,
    "approved": 25,
    "etf approved": 35,
    "bullish": 15,
    "surge": 15,
    "breakout": 15,
    "all-time high": 25,
    "record high": 25,
    "rate cut": 20,
    "partnership": 15,
    "institutional adoption": 25,
    "inflows": 15,
    "accumulate": 15,
    "green light": 20,
    "legal tender": 30,
    "crypto council": 25,
    "pro-crypto": 20,
    "expansion": 10,
    "listing": 15,
}

BEARISH_KEYWORDS = {
    "tariff": 25,
    "tariffs": 25,
    "ban": 30,
    "banned": 30,
    "crackdown": 25,
    "lawsuit": 20,
    "sec lawsuit": 30,
    "hack": 35,
    "hacked": 35,
    "exploit": 30,
    "stolen": 25,
    "subpoena": 20,
    "investigation": 20,
    "rate hike": 25,
    "no rate cut": 25,
    "higher inflation": 25,
    "hotter cpi": 25,
    "hawkish": 20,
    "etf rejected": 35,
    "etf delayed": 25,
    "depeg": 35,
    "depegged": 35,
    "bank run": 35,
    "rug pull": 35,
    "insolvent": 35,
    "insolvency": 35,
    "outage": 20,
    "rejected": 25,
    "denies": 20,
    "denied": 20,
    "delays": 20,
    "delayed": 20,
    "dump": 15,
    "crash": 20,
    "liquidation": 15,
    "bankruptcy": 30,
    "delist": 25,
    "delisting": 25,
    "fud": 10,
    "recession": 20,
}

_NEGATION_WORDS = (
    "not", "no", "never", "without", "reject", "rejects", "rejected",
    "deny", "denies", "denied", "delay", "delays", "delayed",
    "cancel", "cancels", "canceled", "cancelled", "postpone", "postpones",
    "halt", "halts", "halted", "fail", "fails", "failed", "block", "blocks", "blocked",
)

_WHIPSAW_PAUSE_KEYWORDS = (
    "hack", "hacked", "exploit", "stolen", "depeg", "depegged",
    "bankruptcy", "bank run", "insolvent", "insolvency", "flash crash",
    "fomc", "fed rate decision", "cpi report", "emergency rate", "halt",
)

SYMBOL_KEYWORD_MAP = {
    "BTC_USD": ["bitcoin", "btc", "satoshi", "strategic reserve", "bitcoin reserve", "crypto reserve", "mining"],
    "ETH_USD": ["ethereum", "eth", "vitalik", "erc-20", "erc20", "layer 2", "l2"],
    "SOL_USD": ["solana", "sol", "raydium", "pump.fun", "jupiter", "phantom"],
    "TRUMP_USD": ["trump", "donald trump", "white house", "maga", "presidential", "executive order"],
    "PEOPLE_USD": ["constitution", "people", "dao", "election", "campaign"],
    "DOGE_USD": ["doge", "dogecoin", "elon", "musk", "d.o.g.e", "department of government efficiency"],
    "FLOKI_USD": ["floki", "valhalla", "floki inu"],
    "XRP_USD": ["xrp", "ripple", "garlinghouse", "sec vs ripple"],
    "XAU_USD": ["gold", "xau", "precious metal", "safe haven", "inflation hedge"],
    "US30": ["dow jones", "us30", "wall street", "stock market", "fomc", "fed rate", "tariffs"],
    "WIF_USD": ["wif", "dogwifhat"],
    "PEPE_USD": ["pepe", "frog meme"],
    "SHIB_USD": ["shiba", "shib", "shibarium"],
    "BONK_USD": ["bonk"],
    "ANSEM_USD": ["ansem", "the black bull", "hobbes"],
    "BRETT_USD": ["brett", "base chain meme"],
    "BOME_USD": ["bome", "book of meme"],
    "PENGU_USD": ["pengu", "pudgy penguins"],
    "MOG_USD": ["mog", "mog coin"],
    "BNB_USD": ["binance", "bnb", "cz", "changpeng zhao"],
    "AVAX_USD": ["avalanche", "avax"],
    "LINK_USD": ["chainlink", "link", "oracle"],
    "SUI_USD": ["sui", "mysten labs"],
    "NEAR_USD": ["near protocol", "near"],
    "LTC_USD": ["litecoin", "ltc"],
    "ADA_USD": ["cardano", "ada", "hoskinson"],
    "ELON_USD": ["dogelon", "elon mars"],
}


@dataclass
class NewsArticle:
    id: str
    title: str
    url: str
    source: str
    body: str
    published_at: str
    sentiment_score: int          # -100 to +100
    sentiment_label: str          # Bullish, Bearish, Neutral
    impact_level: str             # LOW, MEDIUM, HIGH, CRITICAL
    affected_symbols: list[str]
    published_ts: float | None = None


def analyze_headline_sentiment(title: str, body: str = "") -> tuple[int, str, str, list[str]]:
    """
    Computes sentiment score (-100 to +100), sentiment label, impact level,
    and maps target affected symbols (with negation awareness so flipped headlines
    like 'SEC rejects ETF approval' do not score Bullish).
    """
    text = (title + " " + body).lower()

    bullish_score = 0
    bearish_score = 0
    neg_group = "|".join(_NEGATION_WORDS)

    for kw, weight in BULLISH_KEYWORDS.items():
        kw_pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(kw_pattern, text):
            neg_pattern = r"\b(?:" + neg_group + r")\s+(?:\w+\s+){0,3}" + re.escape(kw) + r"\b"
            if re.search(neg_pattern, text):
                bearish_score += weight
            else:
                bullish_score += weight

    for kw, weight in BEARISH_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            bearish_score += weight

    net_score = max(-100, min(100, (bullish_score - bearish_score) * 2))

    if net_score >= 25:
        sentiment_label = "Bullish"
    elif net_score <= -25:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    total_intensity = bullish_score + bearish_score
    if total_intensity >= 50:
        impact_level = "CRITICAL"
    elif total_intensity >= 30:
        impact_level = "HIGH"
    elif total_intensity >= 15:
        impact_level = "MEDIUM"
    else:
        impact_level = "LOW"

    # Identify affected symbols
    affected = []
    for sym, kws in SYMBOL_KEYWORD_MAP.items():
        if any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in kws):
            affected.append(sym)

    # General market defaults if crypto/macro news
    if not affected:
        if any(w in text for w in ("crypto", "sec", "market", "fomc", "cpi", "fed ", "powell", "rate cut", "rate hike")):
            affected = ["BTC_USD", "ETH_USD"]

    return net_score, sentiment_label, impact_level, affected


import threading

_NEWS_CACHE: list[NewsArticle] = []
_NEWS_CACHE_TIME: float = 0.0
_NEWS_LOCK = threading.Lock()


def fetch_latest_news(limit: int = 15, force_refresh: bool = False) -> list[NewsArticle]:
    """
    Fetches real-time crypto & macro news from live institutional feeds (CoinTelegraph, Decrypt).
    Caches results for 120 seconds to optimize performance and prevent rate limiting.
    """
    import os
    if os.getenv("BACKTEST_MODE") or os.getenv("PYTEST_CURRENT_TEST"):
        return []

    global _NEWS_CACHE, _NEWS_CACHE_TIME
    import time
    now = time.time()
    if not force_refresh and (now - _NEWS_CACHE_TIME) < 120.0 and _NEWS_CACHE:
        return _NEWS_CACHE[:limit]

    with _NEWS_LOCK:
        if not force_refresh and (now - _NEWS_CACHE_TIME) < 120.0 and _NEWS_CACHE:
            return _NEWS_CACHE[:limit]

        _NEWS_CACHE_TIME = now
        rss_feeds = [
            ("CoinTelegraph", "https://cointelegraph.com/rss"),
            ("Decrypt", "https://decrypt.co/feed"),
            ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ]

        articles: list[NewsArticle] = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        for source_name, feed_url in rss_feeds:
            try:
                res = requests.get(feed_url, headers=headers, timeout=1.5)
                if not res.ok:
                    continue

                root = ET.fromstring(res.content)
                items = root.findall(".//item")

                for item in items[:limit]:
                    title_elem = item.find("title")
                    title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

                    link_elem = item.find("link")
                    url = link_elem.text.strip() if link_elem is not None and link_elem.text else ""

                    desc_elem = item.find("description")
                    body = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""
                    body = re.sub(r"<[^>]+>", " ", body)

                    pub_elem = item.find("pubDate")
                    pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else "Recent"
                    pub_ts: float | None = None
                    try:
                        parsed_dt = parsedate_to_datetime(pub_date)
                        if parsed_dt.tzinfo is None:
                            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                        pub_ts = parsed_dt.timestamp()
                        time_part = parsed_dt.astimezone(timezone.utc).strftime("%H:%M UTC")
                    except Exception:
                        try:
                            time_part = pub_date.split()[4][:5] + " UTC"
                        except Exception:
                            time_part = "Recent"

                    if not title:
                        continue

                    score, label, impact, affected = analyze_headline_sentiment(title, body)

                    articles.append(
                        NewsArticle(
                            id=url or title[:30],
                            title=title,
                            url=url,
                            source=source_name,
                            body=body[:250],
                            published_at=time_part,
                            sentiment_score=score,
                            sentiment_label=label,
                            impact_level=impact,
                            affected_symbols=affected,
                            published_ts=pub_ts,
                        )
                    )
            except Exception:
                continue

    impact_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    articles.sort(key=lambda a: impact_order.get(a.impact_level, 4))
    if articles:
        _NEWS_CACHE = articles
    elif _NEWS_CACHE:
        return _NEWS_CACHE[:limit]

    return articles[:limit]


def get_asset_catalyst(symbol: str, max_age_seconds: int = 43200) -> tuple[str, int, str] | None:
    """
    Returns the most impactful fresh catalyst (<= 12h old when timestamped) for a specific symbol:
    (sentiment_label, score, headline) or None if no high-impact catalyst found.
    """
    articles = fetch_latest_news(limit=25)
    now_ts = time.time()
    for art in articles:
        if art.published_ts is not None and (now_ts - art.published_ts) > max_age_seconds:
            continue
        if symbol in art.affected_symbols and art.impact_level in ("HIGH", "CRITICAL") and art.sentiment_label != "Neutral":
            return art.sentiment_label, art.sentiment_score, art.title
    return None


def should_pause_for_news_whipsaw(symbol: str, max_age_seconds: int = 2700) -> tuple[bool, str]:
    """
    Detects fresh (<= 45m) critical market shocks (hacks, exploits, stablecoin depegs,
    emergency macro/FOMC/CPI surprises) where violent two-way wicks trap new entries.
    """
    import os
    if os.getenv("BACKTEST_MODE") or os.getenv("PYTEST_CURRENT_TEST"):
        return False, ""

    articles = fetch_latest_news(limit=25)
    if not articles:
        return False, ""

    now_ts = time.time()
    macro_shocks = ("fomc", "fed rate decision", "cpi report", "emergency rate", "flash crash")
    for art in articles:
        if art.published_ts is None or (now_ts - art.published_ts) > max_age_seconds:
            continue
        text = f"{art.title} {art.body}".lower()
        is_macro_shock = any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in macro_shocks)
        is_asset_shock = (
            symbol in art.affected_symbols
            and art.impact_level == "CRITICAL"
            and any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in _WHIPSAW_PAUSE_KEYWORDS)
        )
        if is_macro_shock or is_asset_shock:
            return True, f"Breaking news whipsaw guard ({art.source}): {art.title[:75]}"
    return False, ""


def format_news_summary(limit: int = 5) -> str:
    """
    Formats the top breaking news stories with sentiment tags for Telegram.
    """
    articles = fetch_latest_news(limit=limit)
    if not articles:
        return "📰 <b>Crypto & Macro News Feed</b>\n\n<i>No breaking catalysts detected at this moment. Market is quiet.</i>"

    lines = [
        "📰 <b>Breaking Crypto, Trump & Macro Catalysts</b>",
        "<i>Real-time Institutional Sentiment Monitor</i>",
        "",
    ]

    for art in articles[:limit]:
        emoji = "🟢" if art.sentiment_label == "Bullish" else "🔴" if art.sentiment_label == "Bearish" else "⚪"
        impact_tag = f"<b>[{art.impact_level} IMPACT]</b> " if art.impact_level in ("HIGH", "CRITICAL") else ""
        symbols_tag = f"🎯 Targets: <code>{', '.join(art.affected_symbols[:4])}</code>" if art.affected_symbols else ""

        safe_title = html.escape(art.title)
        safe_source = html.escape(art.source)

        lines.append(f"{emoji} {impact_tag}<b>{safe_title}</b>")
        lines.append(f"• Sentiment: <code>{art.sentiment_label} ({art.sentiment_score:+} pts)</code> | {art.published_at} (<i>{safe_source}</i>)")
        if symbols_tag:
            lines.append(f"• {symbols_tag}")
        lines.append("")

    return "\n".join(lines).strip()
