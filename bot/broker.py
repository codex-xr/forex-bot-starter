from dataclasses import dataclass, field

@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    units: int
    price: float

@dataclass
class PaperBroker:
    orders: list[Order] = field(default_factory=list)

    def place_order(self, order: Order) -> None:
        self.orders.append(order)

    def open_trade_count(self) -> int:
        return len(self.orders)
