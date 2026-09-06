from __future__ import annotations

import json
from pathlib import Path

from fantasy_store.application.models import (
    OrderDetailView,
    OrderHistoryResult,
    OrderLineView,
    OrderSummaryView,
    snapshot_asset_ref,
)
from fantasy_store.domain.errors import OrderNotFoundError, ValidationError
from fantasy_store.domain.ids import validate_uuid_v4
from fantasy_store.persistence.user_repository import Order, OrderDetail, OrderItemSnapshot, UserRepository
from fantasy_store.snapshot.manager import SnapshotManager


class HistoryService:
    def __init__(self, repository: UserRepository, snapshots: SnapshotManager) -> None:
        self.repository = repository
        self.snapshots = snapshots

    @staticmethod
    def _summary(order: Order) -> OrderSummaryView:
        return OrderSummaryView(
            order_id=order.order_id,
            purchased_at=order.purchased_at,
            total_amount=order.total_amount,
            total_quantity=order.total_quantity,
            line_count=order.line_count,
        )

    def _line(self, item: OrderItemSnapshot) -> OrderLineView:
        try:
            attributes = json.loads(item.attributes_json)
        except json.JSONDecodeError:
            attributes = {}
        if not isinstance(attributes, dict):
            attributes = {}
        available, path = self.snapshots.validate_history_image(
            item.primary_image_snapshot_path,
            item.primary_image_snapshot_sha256,
        )
        opaque = None
        if available and path is not None:
            opaque = snapshot_asset_ref(item.order_id, path.name)
        return OrderLineView(
            line_no=item.line_no,
            pack_id=item.pack_id,
            item_id=item.item_id,
            name=item.item_name,
            unit_price=item.unit_price,
            quantity=item.quantity,
            line_total=item.line_total,
            category=item.category,
            description=item.description,
            attributes=attributes,
            snapshot_image_ref=opaque,
            snapshot_image_available=available,
        )

    def _detail(self, detail: OrderDetail) -> OrderDetailView:
        summary = self._summary(detail.order)
        return OrderDetailView(
            order_id=summary.order_id,
            purchased_at=summary.purchased_at,
            total_amount=summary.total_amount,
            total_quantity=summary.total_quantity,
            line_count=summary.line_count,
            lines=tuple(self._line(item) for item in detail.items),
        )

    def get_order_history(self, page: int = 1, page_size: int = 24) -> OrderHistoryResult:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be >= 1")
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not (1 <= page_size <= 100):
            raise ValidationError("page_size must be 1..100")
        rows = self.repository.list_order_history(page_size, (page - 1) * page_size)
        return OrderHistoryResult(
            orders=tuple(self._summary(order) for order in rows),
            total=self.repository.order_count(),
            page=page,
            page_size=page_size,
        )

    def get_order_detail(self, order_id: str) -> OrderDetailView:
        validate_uuid_v4(order_id)
        detail = self.repository.get_order_detail(order_id)
        if detail is None:
            raise OrderNotFoundError(order_id)
        return self._detail(detail)
