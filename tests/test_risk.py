import pytest
from bot.risk import RiskManager

def test_position_size_uses_account_risk():
    risk = RiskManager(account_balance=10_000, risk_per_trade=0.01)
    assert risk.position_size(stop_loss_pips=25) == 40_000

def test_rejects_too_much_risk():
    with pytest.raises(ValueError):
        RiskManager(account_balance=10_000, risk_per_trade=0.25)
