from __future__ import annotations

from pathlib import Path

from fantasy_store.application.models import PackListResult, PackSummaryView
from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.ids import validate_pack_id
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.persistence.pack_repository import PackRepository


class PackService:
    def __init__(
        self,
        repository: PackRepository,
        coordinator: PackAccessCoordinator,
        lifecycle: PackLifecycleManager,
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator
        self.lifecycle = lifecycle

    def get_packs(self) -> PackListResult:
        rows = self.repository.list_installed_packs()
        summaries = tuple(
            PackSummaryView(
                pack_id=row.pack_id,
                name=row.pack_name,
                version=row.pack_version,
                author=row.author,
                description=row.description,
                enabled=row.is_enabled,
                busy=self.coordinator.is_write_locked(row.pack_id) or self.coordinator.is_recovery_required(row.pack_id),
            )
            for row in rows
        )
        if not summaries:
            state = "NO_PACKS"
        elif not any(row.enabled for row in summaries):
            state = "ALL_PACKS_DISABLED"
        else:
            state = "READY"
        return PackListResult(summaries, state)

    def set_pack_enabled(self, pack_id: str, enabled: bool) -> PackSummaryView:
        validate_pack_id(pack_id)
        if not isinstance(enabled, bool):
            raise ValidationError("enabled must be boolean")
        self.lifecycle.set_pack_enabled(pack_id, enabled)
        for row in self.get_packs().packs:
            if row.pack_id == pack_id:
                return row
        raise ValidationError("pack disappeared after enable/disable")

    def uninstall_pack(self, pack_id: str) -> PackSummaryView:
        validate_pack_id(pack_id)
        row = self.lifecycle.uninstall_pack(pack_id)
        return PackSummaryView(
            pack_id=row.pack_id,
            name=row.pack_name,
            version=row.pack_version,
            author=row.author,
            description=row.description,
            enabled=False,
            busy=False,
        )

    def import_pack_from_native_path(self, source_path: Path):
        """Internal-only Phase 6 delegation boundary; no file dialog/Bridge."""
        if not isinstance(source_path, Path):
            source_path = Path(source_path)
        return self.lifecycle.import_vpack(source_path)
