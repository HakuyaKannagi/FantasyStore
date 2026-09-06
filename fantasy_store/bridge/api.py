from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from fantasy_store.application.cart_service import CartService
from fantasy_store.application.catalog_service import CatalogService
from fantasy_store.application.checkout_service import CheckoutService
from fantasy_store.application.history_service import HistoryService
from fantasy_store.application.pack_service import PackService
from fantasy_store.application.stats_service import StatsService
from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.ids import validate_item_id, validate_pack_id, validate_uuid_v4
from fantasy_store.domain.money import MoneyLiteral
from fantasy_store.pack.importer import ImportKind
from fantasy_store.pack.updater import PackImportSkipped
from .file_picker import FilePicker
from .models import (
    cart_line_dto,
    order_detail_dto,
    order_summary_dto,
    pack_summary_dto,
    product_detail_dto,
    product_summary_dto,
)
from .money_mapper import money_to_dto
from .response import bridge_guard


_SORTS = {"name_asc", "price_asc", "price_desc"}


def _request(value: Any, expected: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("request must be an object")
    if set(value.keys()) != expected:
        raise ValidationError("request fields do not match the fixed DTO")
    return value


def _int(value: Any, label: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValidationError(f"{label} is below the minimum")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{label} exceeds the maximum")
    return value


def _text(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    if len(value) < minimum or (maximum is not None and len(value) > maximum):
        raise ValidationError(f"{label} has an invalid length")
    return value


def _nullable_money(value: Any, label: str) -> Mapping[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be null or MoneyLiteral")
    literal = MoneyLiteral.from_mapping(value)
    return literal.as_dict()


class BridgeApi:
    """Fixed Phase 6 Bridge API. No dynamic dispatch and no arbitrary path input."""

    def __init__(
        self,
        *,
        catalog: CatalogService,
        cart: CartService,
        checkout: CheckoutService,
        history: HistoryService,
        stats: StatsService,
        packs: PackService,
        file_picker: FilePicker,
        logger: logging.Logger | None = None,
    ) -> None:
        self.catalog = catalog
        self.cart = cart
        self.checkout_service = checkout
        self.history = history
        self.stats = stats
        self.packs = packs
        self.file_picker = file_picker
        self.logger = logger or logging.getLogger(__name__)

    def get_products(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"query", "category", "min_price", "max_price", "sort", "page", "page_size"})
            query = _text(req["query"], "query", maximum=200)
            category = req["category"]
            if category is not None:
                category = _text(category, "category", minimum=1, maximum=100)
            min_price = _nullable_money(req["min_price"], "min_price")
            max_price = _nullable_money(req["max_price"], "max_price")
            sort = _text(req["sort"], "sort")
            if sort not in _SORTS:
                raise ValidationError("invalid catalog sort")
            page = _int(req["page"], "page", minimum=1)
            page_size = _int(req["page_size"], "page_size", minimum=1, maximum=100)
            result = self.catalog.get_products(
                query=query,
                category=category,
                min_price=min_price,
                max_price=max_price,
                sort=sort,
                page=page,
                page_size=page_size,
            )
            return {
                "items": [product_summary_dto(item) for item in result.items],
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "catalog_state": result.catalog_state,
            }
        return bridge_guard(self.logger, "get_products", action)

    def get_categories(self) -> dict[str, Any]:
        return bridge_guard(self.logger, "get_categories", lambda: {"categories": list(self.catalog.get_categories())})

    def get_product_detail(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id", "item_id"})
            pack_id = _text(req["pack_id"], "pack_id", minimum=1)
            item_id = _text(req["item_id"], "item_id", minimum=1)
            validate_pack_id(pack_id)
            validate_item_id(item_id)
            return {"product": product_detail_dto(self.catalog.get_product_detail(pack_id, item_id))}
        return bridge_guard(self.logger, "get_product_detail", action)

    def get_cart(self) -> dict[str, Any]:
        def action():
            result = self.cart.get_cart()
            return {
                "lines": [cart_line_dto(line) for line in result.lines],
                "total_amount": money_to_dto(result.total_amount),
                "total_quantity": result.total_quantity,
                "has_unavailable": result.has_unavailable,
            }
        return bridge_guard(self.logger, "get_cart", action)

    def add_to_cart(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id", "item_id", "quantity"})
            pack_id, item_id = self._pack_item(req)
            quantity = _int(req["quantity"], "quantity", minimum=1, maximum=999)
            result = self.cart.add_to_cart(pack_id, item_id, quantity)
            return {"line": cart_line_dto(result.line), "cart_total_quantity": result.cart_total_quantity}
        return bridge_guard(self.logger, "add_to_cart", action)

    def update_cart_item(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id", "item_id", "quantity"})
            pack_id, item_id = self._pack_item(req)
            quantity = _int(req["quantity"], "quantity", minimum=1, maximum=999)
            return {"line": cart_line_dto(self.cart.update_cart_item(pack_id, item_id, quantity))}
        return bridge_guard(self.logger, "update_cart_item", action)

    def remove_cart_item(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id", "item_id"})
            pack_id, item_id = self._pack_item(req)
            return {"removed": bool(self.cart.remove_cart_item(pack_id, item_id))}
        return bridge_guard(self.logger, "remove_cart_item", action)

    def clear_cart(self) -> dict[str, Any]:
        return bridge_guard(self.logger, "clear_cart", lambda: {"cleared_count": self.cart.clear_cart()})

    def checkout(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"request_id"})
            request_id = _text(req["request_id"], "request_id", minimum=1)
            validate_uuid_v4(request_id)
            result = self.checkout_service.checkout(request_id)
            # DB_BACKUP_FAILED is intentionally not an envelope failure because
            # the order is already committed. Expose a non-fatal boolean only
            # as an internal omission: frozen Bridge DTO has exactly two fields.
            if result.backup_failed:
                self.logger.warning(
                    "checkout completed with backup warning",
                    extra={"event_code": "DB_BACKUP_FAILED", "request_id": request_id},
                )
            return {"order": order_detail_dto(result.order), "idempotent_replay": result.idempotent_replay}
        return bridge_guard(self.logger, "checkout", action)

    def get_order_history(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"page", "page_size"})
            page = _int(req["page"], "page", minimum=1)
            page_size = _int(req["page_size"], "page_size", minimum=1, maximum=100)
            result = self.history.get_order_history(page, page_size)
            return {
                "orders": [order_summary_dto(order) for order in result.orders],
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
            }
        return bridge_guard(self.logger, "get_order_history", action)

    def get_order_detail(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"order_id"})
            order_id = _text(req["order_id"], "order_id", minimum=1)
            validate_uuid_v4(order_id)
            return {"order": order_detail_dto(self.history.get_order_detail(order_id))}
        return bridge_guard(self.logger, "get_order_detail", action)

    def get_statistics(self) -> dict[str, Any]:
        def action():
            result = self.stats.get_statistics()
            return {
                "total_amount": money_to_dto(result.total_amount),
                "order_count": result.order_count,
                "total_quantity": result.total_quantity,
            }
        return bridge_guard(self.logger, "get_statistics", action)

    def get_packs(self) -> dict[str, Any]:
        def action():
            result = self.packs.get_packs()
            return {"packs": [pack_summary_dto(pack) for pack in result.packs], "pack_state": result.pack_state}
        return bridge_guard(self.logger, "get_packs", action)

    def import_pack(self) -> dict[str, Any]:
        def action():
            selected = self.file_picker.pick_vpack()
            if selected is None:
                return {"status": "CANCELLED", "pack": None}
            path = Path(selected)
            if path.suffix.lower() != ".vpack" or not path.is_file():
                raise ValidationError("selected file must be an existing .vpack")
            prepared = self.packs.import_pack_from_native_path(path)
            if isinstance(prepared, PackImportSkipped):
                summary = next((p for p in self.packs.get_packs().packs if p.pack_id == prepared.pack_id), None)
                if summary is None:
                    raise RuntimeError("downgrade policy found no current installed pack")
                return {
                    "status": "DOWNGRADE_SKIPPED",
                    "pack": pack_summary_dto(summary),
                    "classification": prepared.classification.value,
                    "incoming_version": prepared.incoming_version,
                    "installed_version": prepared.installed_version,
                }
            status = "UPDATED" if prepared.import_kind == ImportKind.EXISTING_PACK else "IMPORTED"
            pack_id = str(prepared.pack_metadata["pack_id"])
            summary = next((p for p in self.packs.get_packs().packs if p.pack_id == pack_id), None)
            if summary is None:
                raise RuntimeError("import completed without an installed pack summary")
            return {
                "status": status,
                "pack": pack_summary_dto(summary),
                "classification": prepared.version_classification.value if prepared.version_classification else None,
                "incoming_version": str(prepared.pack_metadata["version"]),
                "installed_version": prepared.installed_version_before,
            }
        return bridge_guard(self.logger, "import_pack", action)

    def set_pack_enabled(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id", "enabled"})
            pack_id = _text(req["pack_id"], "pack_id", minimum=1)
            validate_pack_id(pack_id)
            enabled = req["enabled"]
            if not isinstance(enabled, bool):
                raise ValidationError("enabled must be boolean")
            return {"pack": pack_summary_dto(self.packs.set_pack_enabled(pack_id, enabled))}
        return bridge_guard(self.logger, "set_pack_enabled", action)

    @staticmethod
    def _pack_item(req: Mapping[str, Any]) -> tuple[str, str]:
        pack_id = _text(req["pack_id"], "pack_id", minimum=1)
        item_id = _text(req["item_id"], "item_id", minimum=1)
        validate_pack_id(pack_id)
        validate_item_id(item_id)
        return pack_id, item_id
