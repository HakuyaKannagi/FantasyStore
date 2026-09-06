from __future__ import annotations

import json
from typing import Mapping, Any

from fantasy_store.application.models import CatalogResult, ProductDetail, ProductSummary, pack_asset_ref
from fantasy_store.domain.errors import PackBusyError, ProductNotAvailableError, ValidationError
from fantasy_store.domain.ids import validate_item_id, validate_pack_id
from fantasy_store.domain.money import MoneyLiteral, MoneyValue, price_sort_key
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.persistence.pack_repository import PackRepository


_SORTS = {"name_asc", "price_asc", "price_desc"}


def _money_from_row(row) -> MoneyValue:
    literal = MoneyLiteral(str(row["price_significand"]), str(row["price_exponent"]))
    return MoneyValue.from_literal(literal)


def _images(row) -> tuple[str, ...]:
    try:
        raw = json.loads(row["images_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProductNotAvailableError("product image metadata is invalid") from exc
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise ProductNotAvailableError("product image metadata is invalid")
    return tuple(raw)


def _attributes(row) -> dict[str, Any]:
    try:
        raw = json.loads(row["attributes_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProductNotAvailableError("product attributes are invalid") from exc
    if not isinstance(raw, dict):
        raise ProductNotAvailableError("product attributes are invalid")
    return raw


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CatalogService:
    def __init__(
        self,
        repository: PackRepository,
        coordinator: PackAccessCoordinator,
        assets: AssetResolver,
        *,
        lock_timeout: float = 2.0,
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator
        self.assets = assets
        self.lock_timeout = lock_timeout

    @staticmethod
    def _validate_filters(
        query: str,
        category: str | None,
        min_price: Mapping[str, Any] | MoneyLiteral | None,
        max_price: Mapping[str, Any] | MoneyLiteral | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[MoneyLiteral | None, MoneyLiteral | None]:
        if not isinstance(query, str) or len(query) > 200:
            raise ValidationError("query must be a string of at most 200 characters")
        if category is not None and (not isinstance(category, str) or not (1 <= len(category) <= 100)):
            raise ValidationError("category must be null or 1..100 characters")
        if sort not in _SORTS:
            raise ValidationError("invalid catalog sort")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be >= 1")
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not (1 <= page_size <= 100):
            raise ValidationError("page_size must be 1..100")
        min_lit = None if min_price is None else (min_price if isinstance(min_price, MoneyLiteral) else MoneyLiteral.from_mapping(min_price))
        max_lit = None if max_price is None else (max_price if isinstance(max_price, MoneyLiteral) else MoneyLiteral.from_mapping(max_price))
        if min_lit is not None and max_lit is not None:
            if MoneyValue.from_literal(min_lit) > MoneyValue.from_literal(max_lit):
                raise ValidationError("min_price must be <= max_price")
        return min_lit, max_lit

    def _primary_ref_nonblocking(self, row) -> str | None:
        pack_id = str(row["pack_id"])
        images = _images(row)
        if not images:
            return None
        try:
            with self.coordinator.read_lock(pack_id, timeout=0.0):
                self.assets.resolve(pack_id, images[0])
                return pack_asset_ref(pack_id, images[0])
        except PackBusyError:
            return None
        except Exception:
            return None

    def get_products(
        self,
        *,
        query: str = "",
        category: str | None = None,
        min_price: Mapping[str, Any] | MoneyLiteral | None = None,
        max_price: Mapping[str, Any] | MoneyLiteral | None = None,
        sort: str = "name_asc",
        page: int = 1,
        page_size: int = 24,
    ) -> CatalogResult:
        min_lit, max_lit = self._validate_filters(query, category, min_price, max_price, sort, page, page_size)
        installed_count = self.repository.count_installed_packs()
        enabled_count = self.repository.count_enabled_packs()
        pattern = None if query == "" else f"%{_escape_like(query)}%"
        rows, total = self.repository.query_enabled_items(
            query_pattern=pattern,
            category=category,
            min_key=None if min_lit is None else price_sort_key(min_lit),
            max_key=None if max_lit is None else price_sort_key(max_lit),
            sort=sort,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        items = tuple(
            ProductSummary(
                pack_id=str(row["pack_id"]),
                item_id=str(row["item_id"]),
                name=str(row["item_name"]),
                price=_money_from_row(row),
                category=str(row["category"]),
                primary_image_ref=self._primary_ref_nonblocking(row),
            )
            for row in rows
        )
        if installed_count == 0:
            state = "NO_PACKS"
        elif enabled_count == 0:
            state = "ALL_PACKS_DISABLED"
        elif total == 0:
            state = "NO_SEARCH_RESULTS"
        else:
            state = "READY"
        return CatalogResult(items, total, page, page_size, state)

    def get_categories(self) -> tuple[str, ...]:
        return tuple(self.repository.list_enabled_categories())

    def get_product_detail(self, pack_id: str, item_id: str) -> ProductDetail:
        validate_pack_id(pack_id)
        validate_item_id(item_id)
        with self.coordinator.read_lock(pack_id, timeout=self.lock_timeout):
            pack = self.repository.get_installed_pack(pack_id)
            if pack is None or not pack.is_enabled:
                raise ProductNotAvailableError()
            row = self.repository.get_item(pack_id, item_id)
            if row is None:
                raise ProductNotAvailableError()
            images = _images(row)
            refs: list[str] = []
            try:
                for image in images:
                    self.assets.resolve(pack_id, image)
                    refs.append(pack_asset_ref(pack_id, image))
            except Exception as exc:
                raise ProductNotAvailableError("product assets are not available in the current pack generation") from exc
            return ProductDetail(
                pack_id=pack_id,
                item_id=item_id,
                name=str(row["item_name"]),
                price=_money_from_row(row),
                category=str(row["category"]),
                primary_image_ref=refs[0] if refs else None,
                description=str(row["description"]),
                attributes=_attributes(row),
                image_refs=tuple(refs),
            )
