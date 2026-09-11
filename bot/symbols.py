SYMBOL_ALIASES = {
    "US30": "DIA",
    "WIF_USD": "WIF/USDT",
    "BONK_USD": "BONK/USDT",
    "FLOKI_USD": "FLOKI/USDT",
    "TRUMP_USD": "TRUMP/USDT",
    "MOG_USD": "MOG/USDT",
    "PENGU_USD": "PENGU/USDT",
    "BOME_USD": "BOME/USDT",
}

# Binance Spot & Futures symbol mapping for high-speed quantitative data
BINANCE_SYMBOLS = {
    # Major Cryptos (/c1)
    "BTC_USD": {"spot": "BTCUSDT", "futures": "BTCUSDT"},
    "ETH_USD": {"spot": "ETHUSDT", "futures": "ETHUSDT"},
    "SOL_USD": {"spot": "SOLUSDT", "futures": "SOLUSDT"},
    "XRP_USD": {"spot": "XRPUSDT", "futures": "XRPUSDT"},
    "DOGE_USD": {"spot": "DOGEUSDT", "futures": "DOGEUSDT"},
    "ADA_USD": {"spot": "ADAUSDT", "futures": "ADAUSDT"},
    # High-Momentum Altcoins (/c2)
    "BNB_USD": {"spot": "BNBUSDT", "futures": "BNBUSDT"},
    "AVAX_USD": {"spot": "AVAXUSDT", "futures": "AVAXUSDT"},
    "LINK_USD": {"spot": "LINKUSDT", "futures": "LINKUSDT"},
    "SUI_USD": {"spot": "SUIUSDT", "futures": "SUIUSDT"},
    "NEAR_USD": {"spot": "NEARUSDT", "futures": "NEARUSDT"},
    "LTC_USD": {"spot": "LTCUSDT", "futures": "LTCUSDT"},
    # Top Memecoins (/m1)
    "WIF_USD": {"spot": "WIFUSDT", "futures": "WIFUSDT"},
    "PEPE_USD": {"spot": "PEPEUSDT", "futures": "1000PEPEUSDT"},
    "SHIB_USD": {"spot": "SHIBUSDT", "futures": "1000SHIBUSDT"},
    "BONK_USD": {"spot": "BONKUSDT", "futures": "1000BONKUSDT"},
    "FLOKI_USD": {"spot": "FLOKIUSDT", "futures": "1000FLOKIUSDT"},
    "BRETT_USD": {"spot": "BRETTUSDT", "futures": "BRETTUSDT"},
    # Trending & Narrative Memecoins (/m2)
    "TRUMP_USD": {"spot": "TRUMPUSDT", "futures": "TRUMPUSDT"},
    "BOME_USD": {"spot": "BOMEUSDT", "futures": "BOMEUSDT"},
    "PENGU_USD": {"spot": "PENGUUSDT", "futures": "PENGUUSDT"},
    "MOG_USD": {"spot": "MOGUSDT", "futures": "1000MOGUSDT"},
    "PEOPLE_USD": {"spot": "PEOPLEUSDT", "futures": "PEOPLEUSDT"},
    "ELON_USD": {"spot": "ELONUSDT", "futures": "1000ELONUSDT"},
}

DEX_POOLS = {
    "ANSEM_USD": ("solana", "FnzKY6x7entQ1eR3D225dQyT7ybfka4PskBMQhb8L3CC"),
}

DISPLAY_NAMES = {
    # Forex Majors & Crosses
    "EUR_USD": "EURUSD",
    "GBP_USD": "GBPUSD",
    "USD_JPY": "USDJPY",
    "USD_CHF": "USDCHF",
    "AUD_USD": "AUDUSD",
    "USD_CAD": "USDCAD",
    "NZD_USD": "NZDUSD",
    "EUR_GBP": "EURGBP",
    "EUR_JPY": "EURJPY",
    "GBP_JPY": "GBPJPY",
    "GBP_NZD": "GBPNZD",
    "USD_ZAR": "USDZAR",
    "USD_TRY": "USDTRY",
    # Commodities & Indices
    "XAU_USD": "XAUUSD",
    "US30": "US30",
    # Major Cryptos (/c1)
    "BTC_USD": "BTCUSD",
    "ETH_USD": "ETHUSD",
    "SOL_USD": "SOLUSD",
    "XRP_USD": "XRPUSD",
    "DOGE_USD": "DOGEUSD",
    "ADA_USD": "ADAUSD",
    # High-Momentum Altcoins (/c2)
    "BNB_USD": "BNBUSD",
    "AVAX_USD": "AVAXUSD",
    "LINK_USD": "LINKUSD",
    "SUI_USD": "SUIUSD",
    "NEAR_USD": "NEARUSD",
    "LTC_USD": "LTCUSD",
    # Top Memecoins (/m1)
    "WIF_USD": "WIFUSD",
    "PEPE_USD": "PEPEUSD",
    "SHIB_USD": "SHIBUSD",
    "BONK_USD": "BONKUSD",
    "FLOKI_USD": "FLOKIUSD",
    "BRETT_USD": "BRETTUSD",
    "ANSEM_USD": "ANSEM",
    # Trending & Narrative Memecoins (/m2)
    "TRUMP_USD": "TRUMPUSD",
    "BOME_USD": "BOMEUSD",
    "PENGU_USD": "PENGUUSD",
    "MOG_USD": "MOGUSD",
    "PEOPLE_USD": "PEOPLEUSD",
    "ELON_USD": "ELONUSD",
}

PIP_VALUES = {
    "EUR_USD": 0.0001,
    "GBP_USD": 0.0001,
    "USD_JPY": 0.01,
    "USD_CHF": 0.0001,
    "AUD_USD": 0.0001,
    "USD_CAD": 0.0001,
    "NZD_USD": 0.0001,
    "EUR_GBP": 0.0001,
    "EUR_JPY": 0.01,
    "GBP_JPY": 0.01,
    "GBP_NZD": 0.0001,
    "USD_ZAR": 0.0001,
    "USD_TRY": 0.0001,
    "XAU_USD": 0.1,
    "US30": 1.0,
    # Cryptos
    "BTC_USD": 1.0,
    "ETH_USD": 1.0,
    "SOL_USD": 0.01,
    "XRP_USD": 0.0001,
    "DOGE_USD": 0.0001,
    "ADA_USD": 0.0001,
    "BNB_USD": 0.01,
    "AVAX_USD": 0.01,
    "LINK_USD": 0.01,
    "SUI_USD": 0.001,
    "NEAR_USD": 0.001,
    "LTC_USD": 0.01,
    # Memes
    "WIF_USD": 0.0001,
    "PEPE_USD": 0.00000001,
    "SHIB_USD": 0.00000001,
    "BONK_USD": 0.00000001,
    "FLOKI_USD": 0.0000001,
    "BRETT_USD": 0.0001,
    "ANSEM_USD": 0.0001,
    "TRUMP_USD": 0.01,
    "BOME_USD": 0.00001,
    "PENGU_USD": 0.00001,
    "MOG_USD": 0.000000001,
}


def normalize_symbol(symbol: str) -> str:
    """
    Normalizes any user or UI symbol variant (e.g. 'FLOKIUSD', 'FLOKI', 'floki_usd', 'EUR/USD')
    to the canonical system symbol (e.g. 'FLOKI_USD', 'EUR_USD').
    """
    if not symbol:
        return ""
    clean = symbol.strip().upper().replace("/", "_").replace("-", "_").replace(" ", "_")

    # 1. Exact match in DISPLAY_NAMES keys
    if clean in DISPLAY_NAMES:
        return clean

    # 2. Match against DISPLAY_NAMES values (e.g. 'FLOKIUSD' -> 'FLOKI_USD')
    for canonical, display in DISPLAY_NAMES.items():
        if clean == display.upper() or clean.replace("_", "") == display.upper().replace("_", ""):
            return canonical

    # 3. Match against base symbol (e.g. 'FLOKI' -> 'FLOKI_USD', 'BTC' -> 'BTC_USD')
    for canonical in DISPLAY_NAMES:
        base = canonical.split("_")[0]
        if clean == base or clean.replace("_", "") == base:
            return canonical

    # 4. Fallback if ends without _USD
    if not clean.endswith("_USD") and not clean.endswith("_USDT") and clean != "US30":
        trial = f"{clean}_USD"
        if trial in DISPLAY_NAMES:
            return trial

    return clean

