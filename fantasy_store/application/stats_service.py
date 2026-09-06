from __future__ import annotations

from fantasy_store.application.models import StatisticsResult
from fantasy_store.domain.money import sum_money
from fantasy_store.persistence.user_repository import UserRepository


class StatsService:
    def __init__(self, repository: UserRepository) -> None:
        self.repository = repository

    def get_statistics(self) -> StatisticsResult:
        totals = self.repository.list_order_totals()
        return StatisticsResult(
            total_amount=sum_money(amount for amount, _quantity in totals),
            order_count=len(totals),
            total_quantity=sum(quantity for _amount, quantity in totals),
        )
