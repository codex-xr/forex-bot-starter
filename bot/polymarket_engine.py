from dataclasses import dataclass
import html
import json
import re
import time
import requests

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

# Cache recent signals by user_id and market_id for instant 1-tap paper trading
_RECENT_SIGNALS_BY_USER: dict[str, list["PolymarketSignal"]] = {}
_SIGNALS_BY_MARKET_ID: dict[str, "PolymarketSignal"] = {}
_MARKET_CACHE: dict[str, tuple[float, list[dict]]] = {}


@dataclass(frozen=True)
class PolymarketSignal:
    market_id: str
    question: str
    category: str
    action: str          # "BUY YES" or "BUY NO"
    side: str            # "YES" or "NO"
    confidence: int      # 70 - 96 (%)
    entry_price: float   # 0.01 - 0.99 ($)
    fair_price: float    # 0.01 - 0.99 ($)
    edge_pct: float      # Fair probability minus implied probability (%)
    tp1: float           # Target 1 share price ($)
    tp2: float           # Target 2 share price ($)
    sl: float            # Stop loss share price ($)
    tp1_roi_pct: float   # % gain at TP1
    tp2_roi_pct: float   # % gain at TP2
    sl_roi_pct: float    # % loss at SL (negative)
    volume_24h: float
    liquidity: float
    reason: str
    slug: str = ""

    @property
    def url(self) -> str:
        if self.slug:
            return f"https://polymarket.com/event/{self.slug}"
        return "https://polymarket.com"

    def to_message(self, index: int | None = None, bet_size: float = 10.0) -> str:
        idx_prefix = f"#{index} " if index is not None else ""
        safe_q = html.escape(self.question)
        safe_cat = html.escape(self.category)
        safe_reason = html.escape(self.reason)

        entry_cents = self.entry_price * 100.0
        fair_cents = self.fair_price * 100.0
        tp1_cents = self.tp1 * 100.0
        tp2_cents = self.tp2 * 100.0
        sl_cents = self.sl * 100.0

        shares = bet_size / self.entry_price if self.entry_price > 0 else 0.0
        tp1_profit = shares * (self.tp1 - self.entry_price)
        tp2_profit = shares * (self.tp2 - self.entry_price)
        win_profit = shares * (1.0 - self.entry_price)
        win_roi_pct = ((1.0 - self.entry_price) / self.entry_price) * 100.0 if self.entry_price > 0 else 0.0
        sl_loss = shares * (self.entry_price - self.sl)

        side_emoji = "🟢" if self.side == "YES" else "🔴"

        return (
            f"🔮 <b>POLYMARKET SIGNAL {idx_prefix}— <code>{self.confidence}%</code> CONFIDENCE</b>\n"
            f"📌 <b>{safe_q}</b>\n"
            f"🏷️ Category: <b>{safe_cat}</b> | 💧 24h Vol: <code>${self.volume_24h:,.0f}</code>\n"
            f"🎯 <b>Action:</b> {side_emoji} <b>{self.action}</b>\n"
            f"💵 <b>Entry Price:</b> <code>{entry_cents:.1f}¢</code> (<code>${self.entry_price:.3f}</code>)\n"
            f"🧠 <b>Bot Fair Odds:</b> <code>{fair_cents:.1f}¢</code> (<b>+{self.edge_pct:.1f}% Edge</b>)\n"
            f"📈 <b>TP 1 (Cash-Out):</b> <code>{tp1_cents:.1f}¢</code> (<b>+{self.tp1_roi_pct:.1f}%</b> | <code>+${tp1_profit:.2f}</code> on ${bet_size:g})\n"
            f"🚀 <b>TP 2 (Runner):</b> <code>{tp2_cents:.1f}¢</code> (<b>+{self.tp2_roi_pct:.1f}%</b> | <code>+${tp2_profit:.2f}</code> on ${bet_size:g})\n"
            f"🏆 <b>Hold to Finish ($1.00):</b> <b>+{win_roi_pct:.0f}%</b> (<code>+${win_profit:.2f}</code> profit)\n"
            f"🛑 <b>Stop Loss:</b> <code>{sl_cents:.1f}¢</code> (<b>{self.sl_roi_pct:.1f}%</b> | <code>-${sl_loss:.2f}</code> risk)\n"
            f"💡 <b>Reason:</b> {safe_reason}"
        )


def _parse_json_list(val: object) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _detect_category(question: str, raw_market: dict) -> str:
    q = question.lower()
    cat_field = str(raw_market.get("category") or "").strip()
    if cat_field and cat_field.lower() not in ("other", "general", "none"):
        return cat_field.title()

    if any(k in q for k in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp", "doge", "fdv", "airdrop", "token")):
        return "Crypto"
    if any(k in q for k in ("vs.", "vs ", "nba", "nfl", "premier league", "ufc", "champions league", "win on", "match", "arsenal", "real madrid", "lakers", "celtics", "cup", "f1", "grand prix")):
        return "Sports"
    if any(k in q for k in ("trump", "election", "president", "senate", "congress", "governor", "prime minister", "tariffs", "ceasefire", "ukraine", "israel", "china", "iran")):
        return "Politics & Geopolitics"
    if any(k in q for k in ("fed", "interest rate", "inflation", "cpi", "recession", "gdp", "s&p", "nasdaq", "gold", "oil", "tesla", "nvidia", "apple")):
        return "Macro & Finance"
    return "Trending Events"


def _get_crypto_spot_bias(question: str) -> tuple[str | None, str]:
    """
    Cross-checks crypto-related Polymarket questions with live Binance trend & momentum
    to give the user an institutional cross-market edge.
    """
    q = question.lower()
    target_sym = None
    if "bitcoin" in q or "btc" in q:
        target_sym = "BTC_USD"
    elif "ethereum" in q or "eth" in q:
        target_sym = "ETH_USD"
    elif "solana" in q or "sol" in q:
        target_sym = "SOL_USD"
    elif "xrp" in q:
        target_sym = "XRP_USD"
    elif "doge" in q:
        target_sym = "DOGE_USD"

    if not target_sym:
        return None, ""

    try:
        from bot.market_data import fetch_live_candles
        from bot.signal_engine import compute_indicators, htf_bias
        candles = fetch_live_candles(target_sym, outputsize=90)
        if candles is None or len(candles) < 30:
            return None, ""
        df = compute_indicators(candles)
        bias = htf_bias(df)
        last = df.iloc[-1]
        rsi_v = float(last["rsi"]) if "rsi" in last and last["rsi"] == last["rsi"] else 50.0
        rvol_v = float(last["rvol"]) if "rvol" in last and last["rvol"] == last["rvol"] else 1.0
        coin_name = target_sym.split("_")[0]

        # Check if question is negative ("dip", "below", "under", "crash", "drop")
        is_bearish_question = any(w in q for w in ("below", "under", "dip", "drop", "crash", "less than"))

        if bias == "bullish" and rsi_v >= 52:
            favored_side = "NO" if is_bearish_question else "YES"
            return favored_side, f"Binance {coin_name} 4H/15m trend is Bullish (RSI {rsi_v:.0f}, RVol {rvol_v:.1f}x)"
        if bias == "bearish" and rsi_v <= 48:
            favored_side = "YES" if is_bearish_question else "NO"
            return favored_side, f"Binance {coin_name} 4H/15m trend is Bearish (RSI {rsi_v:.0f}, RVol {rvol_v:.1f}x)"
    except Exception:
        pass
    return None, ""


def fetch_raw_polymarket_markets(limit: int = 100, query: str = "") -> list[dict]:
    """
    Fetches live, open, liquid markets from Polymarket's public Gamma API.
    Includes a short 20-second memory cache for sub-second repeat responsiveness.
    """
    cache_key = f"{limit}:{query.strip().lower()}"
    now = time.time()
    if cache_key in _MARKET_CACHE:
        ts, cached_list = _MARKET_CACHE[cache_key]
        if now - ts < 20.0 and cached_list:
            return cached_list

    params = {
        "active": "true",
        "closed": "false",
        "limit": max(limit, 80),
        "order": "volume24hr",
        "ascending": "false",
    }

    last_err = None
    for attempt in range(2):
        try:
            resp = requests.get(
                GAMMA_MARKETS_URL,
                params=params,
                headers={"Accept": "application/json", "User-Agent": "CryptoPredictionBot/2.0"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    _MARKET_CACHE[cache_key] = (now, data)
                    return data
        except Exception as exc:
            last_err = exc
            time.sleep(0.3)

    if last_err:
        print(f"[PolymarketEngine] Fetch error: {last_err}")
    return []


def evaluate_polymarket_market(raw: dict, check_crypto_live: bool = False) -> PolymarketSignal | None:
    """
    Scores a single Polymarket market and produces a PolymarketSignal with Confidence %,
    Entry price, Fair value, TP1, TP2, and Stop Loss if it meets quality standards.
    """
    if raw.get("closed") is True or raw.get("active") is False:
        return None
    if raw.get("acceptingOrders") is False:
        return None

    end_date_str = str(raw.get("endDate") or "").strip()
    if end_date_str:
        try:
            from datetime import datetime, timezone
            clean_iso = end_date_str.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(clean_iso)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt.timestamp() < (time.time() - 3600):
                return None
        except Exception:
            pass

    question = str(raw.get("question") or "").strip()
    market_id = str(raw.get("id") or "").strip()
    if not question or not market_id:
        return None

    outcomes = _parse_json_list(raw.get("outcomes"))
    prices = _parse_json_list(raw.get("outcomePrices"))
    if len(outcomes) < 2 or len(prices) < 2:
        return None

    # Standard Yes/No binary markets
    out0 = str(outcomes[0]).strip().upper()
    out1 = str(outcomes[1]).strip().upper()
    if out0 not in ("YES", "UP", "TRUE") and out1 not in ("NO", "DOWN", "FALSE"):
        # Only trade clear YES/NO binary markets
        return None

    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (ValueError, TypeError):
        return None

    # Normalize if sum is slightly off 1.0
    if yes_price <= 0.01 or yes_price >= 0.99 or no_price <= 0.01 or no_price >= 0.99:
        return None

    vol_24h = float(raw.get("volume24hr") or raw.get("volumeNum") or raw.get("volume") or 0.0)
    liquidity = float(raw.get("liquidityNum") or raw.get("liquidity") or 0.0)

    # Filter out illiquid or dead markets
    if vol_24h < 1500 and liquidity < 3000:
        return None

    day_change = float(raw.get("oneDayPriceChange") or 0.0)
    week_change = float(raw.get("oneWeekPriceChange") or 0.0)
    best_bid = float(raw.get("bestBid") or yes_price)
    best_ask = float(raw.get("bestAsk") or yes_price)
    spread = abs(best_ask - best_bid)

    # Reject markets with wide predatory spreads (> 6 cents)
    if spread > 0.065:
        return None

    category = _detect_category(question, raw)

    # Determine which side (YES vs NO) sits in the asymmetric "Money Zone" (0.14 to 0.78)
    # and has momentum + whale volume confluence
    reasons: list[str] = []
    score_yes = 50.0
    score_no = 50.0

    # 1. Volume-to-Liquidity Velocity (Whale Activity Factor)
    vol_liq_ratio = vol_24h / max(liquidity, 1000.0)
    if vol_liq_ratio >= 1.5:
        vol_bonus = min(14.0, 6.0 + vol_liq_ratio * 2.0)
        reasons.append(f"Heavy whale turnover ({vol_liq_ratio:.1f}x Vol/Liq ratio)")
    elif vol_24h >= 50000:
        vol_bonus = 9.0
        reasons.append(f"Deep institutional volume (${vol_24h:,.0f} 24h)")
    elif vol_24h >= 10000:
        vol_bonus = 5.0
        reasons.append(f"Active order flow (${vol_24h:,.0f} 24h)")
    else:
        vol_bonus = 2.0

    # 2. 24h & 7d Odds Momentum Factor
    if day_change >= 0.03:
        score_yes += min(16.0, day_change * 140.0) + vol_bonus
        reasons.append(f"YES odds surging +{day_change * 100:.1f}¢ today")
    elif day_change <= -0.03:
        score_no += min(16.0, abs(day_change) * 140.0) + vol_bonus
        reasons.append(f"Smart money accumulating NO (YES down {day_change * 100:.1f}¢ today)")
    else:
        # Moderate shift or orderbook skew
        mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else yes_price
        if yes_price >= 0.50:
            score_yes += vol_bonus + 5.0
        else:
            score_no += vol_bonus + 5.0
        if abs(week_change) >= 0.05:
            if week_change > 0:
                score_yes += 7.0
                reasons.append(f"Weekly momentum +{week_change * 100:.1f}¢")
            else:
                score_no += 7.0
                reasons.append(f"Weekly trend favoring NO ({week_change * 100:.1f}¢)")

    # 3. Tight Order-Book Spread & Liquidity Bonus
    if spread <= 0.015 and liquidity >= 15000:
        score_yes += 5.0
        score_no += 5.0
        reasons.append(f"Tight institutional spread ({spread * 100:.1f}¢)")

    # 4. Asymmetric Payout Sweet-Spot Filter (14¢ - 76¢)
    # Buying at 92¢ risks $10 to make $0.80 (bad R:R). Prioritize 15¢ - 75¢!
    if 0.15 <= yes_price <= 0.75:
        score_yes += 10.0
    elif yes_price > 0.82 or yes_price < 0.10:
        score_yes -= 20.0

    if 0.15 <= no_price <= 0.75:
        score_no += 10.0
    elif no_price > 0.82 or no_price < 0.10:
        score_no -= 20.0

    # 5. Optional Live Binance Cross-Market Check for Crypto Markets
    if check_crypto_live and category == "Crypto":
        crypto_side, crypto_reason = _get_crypto_spot_bias(question)
        if crypto_side == "YES":
            score_yes += 12.0
            score_no -= 8.0
            if crypto_reason:
                reasons.insert(0, crypto_reason)
        elif crypto_side == "NO":
            score_no += 12.0
            score_yes -= 8.0
            if crypto_reason:
                reasons.insert(0, crypto_reason)

    # Pick winning side
    if score_yes >= score_no:
        side = "YES"
        action = "BUY YES"
        entry = round(yes_price, 3)
        raw_conf = int(round(score_yes))
    else:
        side = "NO"
        action = "BUY NO"
        entry = round(no_price, 3)
        raw_conf = int(round(score_no))

    # Enforce strict sweet-spot entry range (12¢ to 78¢) so TP1 always has room to pay out handsomely
    if entry < 0.12 or entry > 0.78:
        return None

    confidence = max(65, min(95, raw_conf))

    # Calculate Fair Odds & Edge %
    # Higher confidence = stronger edge over current market price
    edge_cents = max(0.08, min(0.24, (confidence - 60) * 0.0055))
    fair_price = min(0.95, round(entry + edge_cents, 3))
    edge_pct = round((fair_price - entry) * 100.0, 1)

    # Calculate realistic TP1, TP2, and SL share prices
    # Risk distance: ~22% to 28% of entry price (clamped to at least 4¢ and at most 14¢)
    risk_dist = max(0.04, min(0.14, round(entry * 0.25, 3)))
    sl = max(0.02, round(entry - risk_dist, 3))
    actual_risk = max(0.02, entry - sl)

    # TP1 = +1.25R to +1.4R (typically +28% to +40% profit on the bet!)
    tp1 = min(0.94, round(entry + max(actual_risk * 1.30, 0.06), 3))
    # TP2 = +2.4R (runner target before $1.00 resolution)
    tp2 = min(0.97, round(entry + max(actual_risk * 2.40, 0.12), 3))

    tp1_roi = round(((tp1 - entry) / entry) * 100.0, 1)
    tp2_roi = round(((tp2 - entry) / entry) * 100.0, 1)
    sl_roi = round(((sl - entry) / entry) * 100.0, 1)

    if not reasons:
        reasons.append(f"Undervalued {side} odds at {entry * 100:.0f}¢ with strong liquidity depth")

    slug = str(raw.get("slug") or "")
    events = raw.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        event_slug = events[0].get("slug")
        if event_slug:
            slug = str(event_slug)

    sig = PolymarketSignal(
        market_id=market_id,
        question=question,
        category=category,
        action=action,
        side=side,
        confidence=confidence,
        entry_price=entry,
        fair_price=fair_price,
        edge_pct=edge_pct,
        tp1=tp1,
        tp2=tp2,
        sl=sl,
        tp1_roi_pct=tp1_roi,
        tp2_roi_pct=tp2_roi,
        sl_roi_pct=sl_roi,
        volume_24h=vol_24h,
        liquidity=liquidity,
        reason=" | ".join(reasons[:3]),
        slug=slug,
    )
    _SIGNALS_BY_MARKET_ID[market_id] = sig
    return sig


def scan_polymarket_signals(
    query: str = "",
    limit: int = 5,
    min_confidence: int = 70,
    user_id: int | str | None = None,
    bet_size: float = 10.0,
) -> tuple[str, list[PolymarketSignal]]:
    """
    Scans Polymarket for the Top `limit` highest-confidence prediction setups.
    Returns (formatted_html_message, list_of_signals).
    """
    raw_markets = fetch_raw_polymarket_markets(limit=120, query=query)
    if not raw_markets:
        return (
            "⚠️ <b>Polymarket feed is momentarily busy.</b> Please try <code>/poly</code> again in a few seconds.",
            [],
        )

    clean_query = query.strip().lower()
    candidates: list[PolymarketSignal] = []
    seen_questions: set[str] = set()

    for m in raw_markets:
        q_text = str(m.get("question") or "")
        cat_text = str(m.get("category") or "")
        if clean_query:
            if clean_query not in q_text.lower() and clean_query not in cat_text.lower():
                continue

        # Dedup near-identical questions
        q_norm = re.sub(r"[^a-z0-9]", "", q_text.lower())[:45]
        if q_norm in seen_questions:
            continue

        is_crypto_q = any(k in q_text.lower() for k in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol"))
        sig = evaluate_polymarket_market(m, check_crypto_live=is_crypto_q)
        if sig and sig.confidence >= min_confidence:
            seen_questions.add(q_norm)
            candidates.append(sig)

    # Sort by Confidence % descending, then by 24h Volume descending
    candidates.sort(key=lambda s: (s.confidence, s.volume_24h), reverse=True)
    top_picks = candidates[:limit]

    if user_id is not None:
        _RECENT_SIGNALS_BY_USER[str(user_id)] = top_picks
    _RECENT_SIGNALS_BY_USER["global"] = top_picks

    if not top_picks:
        topic_str = f" matching '<b>{html.escape(query)}</b>'" if clean_query else ""
        return (
            f"🔮 <b>Polymarket Prediction Scanner</b>\n\n"
            f"No high-edge setups found{topic_str} right now in the 12¢–78¢ sweet spot.\n"
            f"Try <code>/poly</code> for all categories or <code>/poly crypto</code>, <code>/poly trump</code>, <code>/poly sports</code>.",
            [],
        )

    title_topic = f" — Filter: {html.escape(query.upper())}" if clean_query else " — TOP 5 HIGH-EDGE PICKS"
    lines = [
        f"🔮 <b>POLYMARKET PREDICTION ENGINE{title_topic}</b>",
        f"<i>Bet Size: <code>${bet_size:,.2f}</code> per pick (Change anytime with <code>/polybet &lt;amount&gt;</code>)</i>",
        "",
    ]

    for idx, sig in enumerate(top_picks, start=1):
        lines.append(sig.to_message(index=idx, bet_size=bet_size))
        lines.append("")

    lines.append("👇 <b>Tap a button below to enter a Paper Prediction (Watched 24/7 for TP1 & SL):</b>")
    return "\n".join(lines).strip(), top_picks


def get_cached_signal(user_id: int | str | None, index_or_market_id: str) -> PolymarketSignal | None:
    """
    Retrieves a signal from the user's last /poly scan by 1-based index (e.g. '1'..'5')
    or by Polymarket market_id.
    """
    target = str(index_or_market_id).strip().lstrip("#")
    if target in _SIGNALS_BY_MARKET_ID:
        return _SIGNALS_BY_MARKET_ID[target]

    if target.isdigit():
        idx = int(target) - 1
        uid_key = str(user_id) if user_id is not None else "global"
        user_list = _RECENT_SIGNALS_BY_USER.get(uid_key) or _RECENT_SIGNALS_BY_USER.get("global") or []
        if 0 <= idx < len(user_list):
            return user_list[idx]

    # If not in memory cache (e.g. serverless cold start), fetch market directly by ID
    if target.isdigit() and int(target) > 20:
        m = fetch_market_by_id(target)
        if m:
            sig = evaluate_polymarket_market(m, check_crypto_live=False)
            if sig:
                return sig
    return None


def fetch_market_by_id(market_id: str) -> dict | None:
    """Fetches a specific Polymarket market by its ID from Gamma API."""
    clean_id = str(market_id).strip()
    if not clean_id:
        return None
    for attempt in range(2):
        try:
            resp = requests.get(
                f"{GAMMA_MARKETS_URL}/{clean_id}",
                headers={"Accept": "application/json", "User-Agent": "CryptoPredictionBot/2.0"},
                timeout=4.5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("id"):
                    return data
        except Exception:
            time.sleep(0.2)
    return None


def get_live_share_price(market_id: str, side: str) -> tuple[float | None, bool, str | None]:
    """
    Fetches the current live share price for `side` ('YES' or 'NO') of `market_id`.
    Returns (current_share_price, is_resolved, winning_side).
    """
    m = fetch_market_by_id(market_id)
    if not m:
        return None, False, None

    outcomes = _parse_json_list(m.get("outcomes"))
    prices = _parse_json_list(m.get("outcomePrices"))
    if len(outcomes) < 2 or len(prices) < 2:
        return None, False, None

    try:
        yes_p = float(prices[0])
        no_p = float(prices[1])
    except (ValueError, TypeError):
        return None, False, None

    clean_side = side.strip().upper()
    curr_price = yes_p if clean_side == "YES" else no_p

    is_closed = bool(m.get("closed"))
    winning_side = None
    if is_closed:
        if yes_p >= 0.98:
            winning_side = "YES"
        elif no_p >= 0.98:
            winning_side = "NO"

    return curr_price, is_closed, winning_side
