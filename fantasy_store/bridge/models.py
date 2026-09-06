from __future__ import annotations

from fantasy_store.application.models import (
    CartLine,
    OrderDetailView,
    OrderLineView,
    OrderSummaryView,
    PackSummaryView,
    ProductDetail,
    ProductSummary,
)
from .money_mapper import money_to_dto


def product_summary_dto(value: ProductSummary) -> dict[str, object]:
    return {
        "pack_id": value.pack_id,
        "item_id": value.item_id,
        "name": value.name,
        "price": money_to_dto(value.price),
        "category": value.category,
        "primary_image_ref": value.primary_image_ref,
    }


def product_detail_dto(value: ProductDetail) -> dict[str, object]:
    result = product_summary_dto(value)
    result.update({
        "description": value.description,
        "attributes": dict(value.attributes),
        "image_refs": list(value.image_refs),
    })
    return result


def cart_line_dto(value: CartLine) -> dict[str, object]:
    return {
        "pack_id": value.pack_id,
        "item_id": value.item_id,
        "quantity": value.quantity,
        "available": value.available,
        "unavailable_reason": value.unavailable_reason,
        "name": value.name,
        "unit_price": None if value.unit_price is None else money_to_dto(value.unit_price),
        "line_total": None if value.line_total is None else money_to_dto(value.line_total),
        "primary_image_ref": value.primary_image_ref,
    }


def pack_summary_dto(value: PackSummaryView) -> dict[str, object]:
    return {
        "pack_id": value.pack_id,
        "name": value.name,
        "version": value.version,
        "author": value.author,
        "description": value.description,
        "enabled": value.enabled,
        "busy": value.busy,
    }


def order_summary_dto(value: OrderSummaryView) -> dict[str, object]:
    return {
        "order_id": value.order_id,
        "purchased_at": value.purchased_at,
        "total_amount": money_to_dto(value.total_amount),
        "total_quantity": value.total_quantity,
        "line_count": value.line_count,
    }


def order_line_dto(value: OrderLineView) -> dict[str, object]:
    return {
        "line_no": value.line_no,
        "pack_id": value.pack_id,
        "item_id": value.item_id,
        "name": value.name,
        "unit_price": money_to_dto(value.unit_price),
        "quantity": value.quantity,
        "line_total": money_to_dto(value.line_total),
        "category": value.category,
        "description": value.description,
        "attributes": dict(value.attributes),
        "snapshot_image_ref": value.snapshot_image_ref,
        "snapshot_image_available": value.snapshot_image_available,
    }


def order_detail_dto(value: OrderDetailView) -> dict[str, object]:
    result = order_summary_dto(value)
    result["lines"] = [order_line_dto(line) for line in value.lines]
    return result
