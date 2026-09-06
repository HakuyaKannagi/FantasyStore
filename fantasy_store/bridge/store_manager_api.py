from __future__ import annotations

from typing import Any

from fantasy_store.application.history_maintenance_service import HistoryMaintenanceService
from fantasy_store.bridge.api import BridgeApi, _request, _text
from fantasy_store.bridge.models import pack_summary_dto
from fantasy_store.bridge.response import bridge_guard
from fantasy_store.domain.ids import validate_pack_id


class StoreManagerBridgeApi(BridgeApi):
    """Human-approved manager-only Bridge extension.

    The frozen 15 store Bridge methods remain unchanged. Manager-only methods
    are exposed only when Python starts with --store-manager.
    """

    def __init__(self, *, history_maintenance: HistoryMaintenanceService | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history_maintenance = history_maintenance

    def uninstall_pack(self, request: object) -> dict[str, Any]:
        def action():
            req = _request(request, {"pack_id"})
            pack_id = _text(req["pack_id"], "pack_id", minimum=1)
            validate_pack_id(pack_id)
            return {"pack": pack_summary_dto(self.packs.uninstall_pack(pack_id))}

        return bridge_guard(self.logger, "uninstall_pack", action)

    def reset_purchase_history(self) -> dict[str, Any]:
        def action():
            if self.history_maintenance is None:
                raise RuntimeError("history maintenance boundary is not configured")
            result = self.history_maintenance.reset_all()
            return {
                "deleted_orders": result.deleted_orders,
                "removed_snapshot_directories": result.removed_snapshot_directories,
                "failed_snapshot_directories": result.failed_snapshot_directories,
            }

        return bridge_guard(self.logger, "reset_purchase_history", action)
