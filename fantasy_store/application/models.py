from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fantasy_store.domain.money import MoneyValue


@dataclass(frozen=True, slots=True)
class ProductSummary:
    pack_id: str
    item_id: str
    name: str
    price: MoneyValue
    category: str
    primary_image_ref: str | None


@dataclass(frozen=True, slots=True)
class ProductDetail(ProductSummary):
    description: str
    attributes: dict[str, Any]
    image_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogResult:
    items: tuple[ProductSummary, ...]
    total: int
    page: int
    page_size: int
    catalog_state: str


@dataclass(frozen=True, slots=True)
class CartLine:
    pack_id: str
    item_id: str
    quantity: int
    available: bool
    unavailable_reason: str | None
    name: str | None
    unit_price: MoneyValue | None
    line_total: MoneyValue | None
    primary_image_ref: str | None


@dataclass(frozen=True, slots=True)
class CartResult:
    lines: tuple[CartLine, ...]
    total_amount: MoneyValue
    total_quantity: int
    has_unavailable: bool


@dataclass(frozen=True, slots=True)
class AddCartResult:
    line: CartLine
    cart_total_quantity: int


@dataclass(frozen=True, slots=True)
class OrderSummaryView:
    order_id: str
    purchased_at: str
    total_amount: MoneyValue
    total_quantity: int
    line_count: int


@dataclass(frozen=True, slots=True)
class OrderLineView:
    line_no: int
    pack_id: str
    item_id: str
    name: str
    unit_price: MoneyValue
    quantity: int
    line_total: MoneyValue
    category: str
    description: str
    attributes: dict[str, Any]
    snapshot_image_ref: str | None
    snapshot_image_available: bool


@dataclass(frozen=True, slots=True)
class OrderDetailView(OrderSummaryView):
    lines: tuple[OrderLineView, ...]


@dataclass(frozen=True, slots=True)
class OrderHistoryResult:
    orders: tuple[OrderSummaryView, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    order: OrderDetailView
    idempotent_replay: bool
    backup_failed: bool = False


@dataclass(frozen=True, slots=True)
class StatisticsResult:
    total_amount: MoneyValue
    order_count: int
    total_quantity: int


@dataclass(frozen=True, slots=True)
class PackSummaryView:
    pack_id: str
    name: str
    version: str
    author: str
    description: str
    enabled: bool
    busy: bool


@dataclass(frozen=True, slots=True)
class PackListResult:
    packs: tuple[PackSummaryView, ...]
    pack_state: str


def pack_asset_ref(pack_id: str, image_reference: str) -> str:
    # Opaque internal reference: deliberately not an OS path.
    return f"pack-asset:{pack_id}:{image_reference}"


def snapshot_asset_ref(order_id: str, filename: str) -> str:
    return f"snapshot:{order_id}:{filename}"
