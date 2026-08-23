import argparse
from pathlib import Path

from bot.broker import Order, PaperBroker
from bot.config import load_settings
from bot.data import load_price_data
from bot.risk import RiskManager
from bot.strategy import MovingAverageCrossover, Signal
from bot.telegram import send_telegram_message
from bot.symbols import PIP_VALUES

def run_backtest(data_path: Path) -> None:
    settings = load_settings()
    prices = load_price_data(data_path)
    strategy = MovingAverageCrossover(settings.fast_ma, settings.slow_ma)
    risk = RiskManager(settings.account_balance, settings.risk_per_trade, settings.max_open_trades)
    broker = PaperBroker()

    signals = strategy.generate(prices)
    for index, signal in signals.items():
        if signal == Signal.HOLD.value:
            continue
        if not risk.can_open_trade(broker.open_trade_count()):
            continue

        row = prices.loc[index]
        pip_val = PIP_VALUES.get(settings.symbol, 0.0001)
        units = risk.position_size(stop_loss_pips=25, pip_value_per_unit=pip_val)
        order = Order(settings.symbol, signal, units, float(row["close"]))
        broker.place_order(order)

        send_telegram_message(
            f"Forex Bot Signal\n"
            f"Pair: {order.symbol}\n"
            f"Signal: {order.side.upper()}\n"
            f"Units: {order.units}\n"
            f"Price: {order.price:.5f}\n"
            f"Mode: Backtest"
        )

    print(f"Loaded {len(prices)} candles")
    print(f"Generated {len(broker.orders)} paper orders")
    for order in broker.orders:
        print(f"{order.side.upper()} {order.units} {order.symbol} @ {order.price:.5f}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Forex bot starter")
    parser.add_argument("--mode", choices=["backtest", "paper"], default="backtest")
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()

    if args.mode != "backtest":
        raise SystemExit("Only backtest mode is implemented in this safe starter.")

    run_backtest(args.data)

if __name__ == "__main__":
    main()
