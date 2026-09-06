from __future__ import annotations

from threading import Condition
from typing import Any, Callable

from fantasy_store.bridge.response import failure


class ManagedBridgeApi:
    """Close-aware proxy exposing exactly the frozen 15 Bridge methods.

    pywebview executes exposed API calls on worker threads.  This proxy blocks new
    calls once shutdown begins and lets the runtime wait until already-started calls
    complete before persistent resources and the instance lock are released.
    """

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge
        self._condition = Condition()
        self._accepting = True
        self._active = 0

    def _invoke(self, fn: Callable[..., dict], *args: Any) -> dict:
        with self._condition:
            if not self._accepting:
                return failure("INTERNAL_ERROR", "アプリを終了しています。")
            self._active += 1
        try:
            return fn(*args)
        finally:
            with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    def _begin_shutdown(self) -> None:
        with self._condition:
            self._accepting = False
            self._condition.notify_all()

    def _wait_for_idle(self) -> None:
        with self._condition:
            while self._active:
                self._condition.wait()

    def get_products(self, request: dict) -> dict:
        return self._invoke(self._bridge.get_products, request)

    def get_categories(self) -> dict:
        return self._invoke(self._bridge.get_categories)

    def get_product_detail(self, request: dict) -> dict:
        return self._invoke(self._bridge.get_product_detail, request)

    def get_cart(self) -> dict:
        return self._invoke(self._bridge.get_cart)

    def add_to_cart(self, request: dict) -> dict:
        return self._invoke(self._bridge.add_to_cart, request)

    def update_cart_item(self, request: dict) -> dict:
        return self._invoke(self._bridge.update_cart_item, request)

    def remove_cart_item(self, request: dict) -> dict:
        return self._invoke(self._bridge.remove_cart_item, request)

    def clear_cart(self) -> dict:
        return self._invoke(self._bridge.clear_cart)

    def checkout(self, request: dict) -> dict:
        return self._invoke(self._bridge.checkout, request)

    def get_order_history(self, request: dict) -> dict:
        return self._invoke(self._bridge.get_order_history, request)

    def get_order_detail(self, request: dict) -> dict:
        return self._invoke(self._bridge.get_order_detail, request)

    def get_statistics(self) -> dict:
        return self._invoke(self._bridge.get_statistics)

    def get_packs(self) -> dict:
        return self._invoke(self._bridge.get_packs)

    def import_pack(self) -> dict:
        return self._invoke(self._bridge.import_pack)

    def set_pack_enabled(self, request: dict) -> dict:
        return self._invoke(self._bridge.set_pack_enabled, request)


class ManagedStoreManagerBridgeApi(ManagedBridgeApi):
    """Close-aware manager-only extension; never used by normal store mode."""

    def uninstall_pack(self, request: dict) -> dict:
        return self._invoke(self._bridge.uninstall_pack, request)

    def reset_purchase_history(self) -> dict:
        return self._invoke(self._bridge.reset_purchase_history)
