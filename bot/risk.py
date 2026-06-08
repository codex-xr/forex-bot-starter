from dataclasses import dataclass

@dataclass(frozen=True)
class RiskManager:
    account_balance: float
    risk_per_trade: float = 0.01
    max_open_trades: int = 1

    def __post_init__(self):
        if self.account_balance <= 0:
            raise ValueError("account_balance must be positive")
        if not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("risk_per_trade must be between 0 and 0.05")
        if self.max_open_trades < 1:
            raise ValueError("max_open_trades must be at least 1")

    def position_size(self, stop_loss_pips: float, pip_value_per_unit: float = 0.0001) -> int:
        if stop_loss_pips <= 0:
            raise ValueError("stop_loss_pips must be positive")
        risk_amount = self.account_balance * self.risk_per_trade
        return max(1, int(risk_amount / (stop_loss_pips * pip_value_per_unit)))

    def can_open_trade(self, open_trades: int) -> bool:
        return open_trades < self.max_open_trades
