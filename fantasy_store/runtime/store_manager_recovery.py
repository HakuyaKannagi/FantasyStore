from __future__ import annotations

from dataclasses import dataclass
from html import escape
import base64
import hashlib
import importlib
import os
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from fantasy_store.domain.errors import DomainError, PackRecoveryRequiredError, PackSafetyDiagnostic, PackValidationError
from fantasy_store.domain.ids import validate_pack_id
from fantasy_store.domain.pack_version import PackVersion
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackOperationJournal
from fantasy_store.pack.recovery import RecoveryDecision


APP_TITLE = "FantasyStore — 店長モード（復旧）"


@dataclass(frozen=True, slots=True)
class RecoveryMaintenanceStatus:
    pack_id: str
    pack_name: str | None
    pack_version: str | None
    enabled: bool | None
    safety_level: str
    can_disable: bool
    can_uninstall: bool
    reason: str
    restart_required: bool = False

    @property
    def maintenance_allowed(self) -> bool:
        return self.safety_level == "SAFE_MAINTENANCE"

    def to_public(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "enabled": self.enabled,
            "safety_level": self.safety_level,
            "can_disable": self.can_disable,
            "can_uninstall": self.can_uninstall,
            "reason": self.reason,
            "restart_required": self.restart_required,
        }


@dataclass(frozen=True, slots=True)
class RecoveryDisplayRow:
    category: str
    pack_id: str
    pack_name: str | None
    pack_version: str | None
    operation_id: str | None
    operation_type: str | None
    state: str
    message: str
    safety_level: str
    enabled: bool | None
    can_disable: bool
    can_uninstall: bool
    maintenance_reason: str


class StoreManagerRecoveryMaintenance:
    """Observation-gated restricted maintenance for Pack startup failures.

    This is intentionally not a generic repair service.  It permits only the
    Human-approved Legacy Version retirement path and re-observes Pack identity,
    DB/filesystem digest equality, Recovery state, and enabled state immediately
    before each lifecycle write.
    """

    def __init__(self, phase4_runtime: Any) -> None:
        self.phase4 = phase4_runtime
        self.repository = phase4_runtime.pack_lifecycle.repository
        self.lifecycle = phase4_runtime.pack_lifecycle
        self.coordinator = phase4_runtime.pack_access
        self.resolver = phase4_runtime.pack_recovery.resolver
        self.logger = phase4_runtime.logger
        self._lock = RLock()
        self._diagnostics = {
            d.pack_id: d for d in phase4_runtime.pack_safety_failures if d.pack_id is not None
        }

    def _diagnostic(self, pack_id: str) -> PackSafetyDiagnostic | None:
        return self._diagnostics.get(pack_id)

    def _recovery_is_healthy(self) -> bool:
        return not any(
            d.final_state == "RECOVERY_REQUIRED"
            for d in self.phase4.pack_recovery_decisions
        )

    def _diagnostic_only(self, pack_id: str, reason: str, *, row=None) -> RecoveryMaintenanceStatus:
        return RecoveryMaintenanceStatus(
            pack_id=pack_id,
            pack_name=getattr(row, "pack_name", None),
            pack_version=getattr(row, "pack_version", None),
            enabled=getattr(row, "is_enabled", None),
            safety_level="DIAGNOSTIC_ONLY",
            can_disable=False,
            can_uninstall=False,
            reason=reason,
        )

    def assess(self, pack_id: str) -> RecoveryMaintenanceStatus:
        """Re-observe whether one Pack is safe for restricted retirement only."""
        try:
            pack_id = validate_pack_id(pack_id)
        except Exception:
            return self._diagnostic_only(str(pack_id), "Pack IDを安全に確認できません。")

        diagnostic = self._diagnostic(pack_id)
        if diagnostic is None:
            return self._diagnostic_only(pack_id, "このPackは現在の復旧診断対象ではありません。")

        if not diagnostic.safe_maintenance_allowed:
            return self._diagnostic_only(pack_id, "現在状態を安全に確定できないため、状態確認のみ可能です。")

        if not self._recovery_is_healthy():
            return self._diagnostic_only(pack_id, "未解決のPack Recoveryがあるため、保守操作を開始できません。")

        if self.coordinator.is_recovery_required(pack_id):
            return self._diagnostic_only(pack_id, "PackがRECOVERY_REQUIREDのため、保守操作を開始できません。")

        row = self.repository.get_installed_pack(pack_id)
        if row is None:
            return RecoveryMaintenanceStatus(
                pack_id=pack_id,
                pack_name=diagnostic.pack_name,
                pack_version=diagnostic.pack_version,
                enabled=None,
                safety_level="RESOLVED_RESTART_REQUIRED",
                can_disable=False,
                can_uninstall=False,
                reason="対象Packは現在導入済みではありません。アプリを再起動してください。",
                restart_required=True,
            )

        # The restricted path is only for a still-legacy Version.  If the row was
        # externally changed into canonical form, do not infer that startup is now
        # healthy inside this already-restricted runtime; require a fresh restart.
        try:
            PackVersion.parse(row.pack_version)
        except PackValidationError:
            pass
        else:
            return RecoveryMaintenanceStatus(
                pack_id=pack_id,
                pack_name=row.pack_name,
                pack_version=row.pack_version,
                enabled=row.is_enabled,
                safety_level="RESOLVED_RESTART_REQUIRED",
                can_disable=False,
                can_uninstall=False,
                reason="Version不整合は解消されています。状態を再確認するためアプリを再起動してください。",
                restart_required=True,
            )

        # Do not accept a different legacy string than the startup diagnostic.
        if diagnostic.pack_version is not None and row.pack_version != diagnostic.pack_version:
            return self._diagnostic_only(pack_id, "起動後にPack Version状態が変化したため、保守操作を停止しました。", row=row)

        installed = self.resolver.installed_pack_root(pack_id)
        expected_rel = self.resolver.relative_to_persistent(installed)
        if row.install_dir != expected_rel or not installed.is_dir():
            return self._diagnostic_only(pack_id, "Pack管理情報と導入済みfilesystemを安全に一致確認できません。", row=row)

        try:
            fs_digest = compute_content_digest(installed)
        except Exception:
            return self._diagnostic_only(pack_id, "導入済みPackの内容世代を安全に観測できません。", row=row)
        if fs_digest != row.content_digest:
            return self._diagnostic_only(pack_id, "DBと導入済みPackの内容世代が一致しません。", row=row)

        # Category is necessary but not sufficient: all observation evidence above
        # must still hold immediately before a write.
        if diagnostic.category != "PACK_VERSION_SCHEMA_INCOMPATIBLE":
            return self._diagnostic_only(pack_id, "この異常種別では保守書込みを許可できません。", row=row)

        return RecoveryMaintenanceStatus(
            pack_id=pack_id,
            pack_name=row.pack_name,
            pack_version=row.pack_version,
            enabled=row.is_enabled,
            safety_level="SAFE_MAINTENANCE",
            can_disable=bool(row.is_enabled),
            can_uninstall=not bool(row.is_enabled),
            reason=(
                "現在generationとDB/filesystemの一致を確認済みです。"
                "Legacy Version Packを安全に退役させる操作だけ実行できます。"
            ),
        )

    @staticmethod
    def _require_allowed(status: RecoveryMaintenanceStatus, action: str) -> None:
        allowed = status.can_disable if action == "disable" else status.can_uninstall
        if not status.maintenance_allowed or not allowed:
            raise PackRecoveryRequiredError(status.pack_id, "restricted maintenance is not allowed for the observed state")

    def disable_pack(self, pack_id: str) -> RecoveryMaintenanceStatus:
        with self._lock:
            status = self.assess(pack_id)
            self._require_allowed(status, "disable")
            self.lifecycle.set_pack_enabled(status.pack_id, False)
            refreshed = self.assess(status.pack_id)
            self.logger.warning(
                "restricted store-manager disabled legacy Pack",
                extra={"event_code": "STORE_MANAGER_RECOVERY_DISABLE", "pack_id": status.pack_id},
            )
            return refreshed

    def uninstall_pack(self, pack_id: str) -> RecoveryMaintenanceStatus:
        with self._lock:
            status = self.assess(pack_id)
            self._require_allowed(status, "uninstall")
            self.lifecycle.uninstall_pack(status.pack_id)
            self.logger.warning(
                "restricted store-manager uninstalled legacy Pack",
                extra={"event_code": "STORE_MANAGER_RECOVERY_UNINSTALL", "pack_id": status.pack_id},
            )
            return RecoveryMaintenanceStatus(
                pack_id=status.pack_id,
                pack_name=status.pack_name,
                pack_version=status.pack_version,
                enabled=None,
                safety_level="RESOLVED_RESTART_REQUIRED",
                can_disable=False,
                can_uninstall=False,
                reason="商品Packをアンインストールしました。通常bootstrapで再確認するためアプリを再起動してください。",
                restart_required=True,
            )


class StoreManagerRecoveryApi:
    """Minimal restricted pywebview API; not the normal 15-method storefront Bridge."""

    def __init__(self, maintenance: StoreManagerRecoveryMaintenance, *, logger=None) -> None:
        self._maintenance = maintenance
        self._logger = logger

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": data, "error": None}

    @staticmethod
    def _safe_error(code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "data": None, "error": {"code": code, "message": message}}

    def _run(self, action: str, pack_id: str) -> dict[str, Any]:
        try:
            pack_id = validate_pack_id(pack_id)
            if action == "disable":
                status = self._maintenance.disable_pack(pack_id)
                result = "DISABLED"
            else:
                status = self._maintenance.uninstall_pack(pack_id)
                result = "UNINSTALLED"
            return self._ok({"status": result, "maintenance": status.to_public()})
        except DomainError as exc:
            if self._logger:
                self._logger.warning(
                    "restricted maintenance rejected code=%s pack_id=%s",
                    exc.code,
                    pack_id if isinstance(pack_id, str) else "-",
                    extra={"event_code": "STORE_MANAGER_RECOVERY_ACTION_REJECTED"},
                )
            message = {
                "PACK_BUSY": "商品Packが処理中です。しばらくしてから再度お試しください。",
                "PACK_UNINSTALL_REQUIRES_DISABLED": "アンインストールするには先に商品Packを無効にしてください。",
                "VALIDATION_INVALID_ARGUMENT": "Pack IDが正しくありません。",
                "RECOVERY_REQUIRED": "この状態では安全な保守操作を実行できません。",
            }.get(exc.code, "保守操作を安全に完了できませんでした。")
            return self._safe_error(exc.code, message)
        except Exception:
            if self._logger:
                self._logger.exception(
                    "unexpected restricted maintenance failure",
                    extra={"event_code": "STORE_MANAGER_RECOVERY_ACTION_FAILED"},
                )
            return self._safe_error("INTERNAL_ERROR", "保守操作を安全に完了できませんでした。")

    def disable_pack(self, pack_id: str) -> dict[str, Any]:
        return self._run("disable", pack_id)

    def uninstall_pack(self, pack_id: str) -> dict[str, Any]:
        return self._run("uninstall", pack_id)


def build_recovery_rows(
    journals: Iterable[PackOperationJournal],
    decisions: Iterable[RecoveryDecision],
    *,
    safety_failures: Iterable[PackSafetyDiagnostic] = (),
    maintenance_statuses: Mapping[str, RecoveryMaintenanceStatus] | None = None,
) -> tuple[RecoveryDisplayRow, ...]:
    """Build safe UI rows from structured Pack startup diagnostics."""
    maintenance_statuses = maintenance_statuses or {}
    diagnostics = tuple(safety_failures)
    if diagnostics:
        rows: list[RecoveryDisplayRow] = []
        for d in diagnostics:
            key = d.pack_id or ""
            status = maintenance_statuses.get(key)
            rows.append(
                RecoveryDisplayRow(
                    category=d.category,
                    pack_id=d.pack_id or "(未確定)",
                    pack_name=(status.pack_name if status else d.pack_name),
                    pack_version=(status.pack_version if status else d.pack_version),
                    operation_id=d.operation_id,
                    operation_type=d.operation_type,
                    state=d.state,
                    message=d.message,
                    safety_level=status.safety_level if status else "DIAGNOSTIC_ONLY",
                    enabled=status.enabled if status else d.is_enabled,
                    can_disable=status.can_disable if status else False,
                    can_uninstall=status.can_uninstall if status else False,
                    maintenance_reason=status.reason if status else "状態確認のみ可能です。",
                )
            )
        return tuple(rows)

    by_id = {j.operation_id: j for j in journals}
    rows: list[RecoveryDisplayRow] = []
    for decision in decisions:
        if decision.final_state != "RECOVERY_REQUIRED":
            continue
        journal = by_id.get(decision.operation_id)
        rows.append(
            RecoveryDisplayRow(
                category="PACK_RECOVERY_REQUIRED",
                pack_id=(journal.pack_id if journal else decision.pack_id) or "(未確定)",
                pack_name=None,
                pack_version=(journal.new_version or journal.old_version) if journal else None,
                operation_id=decision.operation_id,
                operation_type=journal.operation_type if journal else None,
                state=journal.state if journal else "RECOVERY_REQUIRED",
                message="Pack操作の復旧状態を安全に確定できません。",
                safety_level="DIAGNOSTIC_ONLY",
                enabled=None,
                can_disable=False,
                can_uninstall=False,
                maintenance_reason="現在generationを安全に確定できないため、状態確認のみ可能です。",
            )
        )
    return tuple(rows)


_RECOVERY_STYLE = """
:root{color-scheme:light;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:#f4f6f8;color:#20242a}
body{line-height:1.6}
main{max-width:1180px;margin:0 auto;padding:32px 28px 48px}
h1{margin:0 0 8px;color:#172033;font-size:30px}
h2{margin-top:30px;color:#24324a;font-size:21px}
p{max-width:92ch}
.notice{background:#fff7e6;border:1px solid #e5b85c;border-left:5px solid #b87800;border-radius:8px;padding:14px 16px;color:#4c3608}
.status-safe{color:#17612b;font-weight:700}
.status-diagnostic{color:#8a3b12;font-weight:700}
.table-wrap{width:100%;overflow-x:auto;background:#fff;border:1px solid #cfd6df;border-radius:8px}
table{width:100%;border-collapse:collapse;table-layout:fixed;background:#fff;color:#20242a}
th{background:#e7ecf2;color:#172033;text-align:left;font-weight:700}
th,td{padding:10px 12px;border-bottom:1px solid #d8dee7;border-right:1px solid #e2e6ec;vertical-align:top;overflow-wrap:anywhere;word-break:break-word;min-width:0}
th:last-child,td:last-child{border-right:0}
tr:last-child td{border-bottom:0}
.actions{display:flex;flex-direction:column;gap:8px;min-width:150px}
button{font:inherit;border-radius:6px;border:1px solid #8b98a9;padding:8px 10px;background:#fff;color:#172033;cursor:pointer}
button.primary{background:#8b4b00;border-color:#8b4b00;color:#fff}
button.danger{background:#9b2c2c;border-color:#9b2c2c;color:#fff}
button:disabled{opacity:.45;cursor:not-allowed}
.feedback{display:block;margin-top:6px;color:#31415a;font-size:13px;overflow-wrap:anywhere}
.restart{margin-top:20px;padding:12px 14px;border-radius:7px;background:#eaf4ec;color:#1e5d2e;border:1px solid #a9cfb1}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
@media(max-width:900px){main{padding:22px 14px}table{min-width:900px}}
""".strip()


_RECOVERY_SCRIPT = r"""
(() => {
  'use strict';
  const setFeedback = (row, text) => {
    const node = row.querySelector('[data-feedback]');
    if (node) node.textContent = text;
  };
  const updateAfterDisable = (row) => {
    const enabled = row.querySelector('[data-enabled]');
    if (enabled) enabled.textContent = '無効';
    const disable = row.querySelector('[data-action="disable"]');
    const uninstall = row.querySelector('[data-action="uninstall"]');
    if (disable) disable.disabled = true;
    if (uninstall) uninstall.disabled = false;
    setFeedback(row, '無効化しました。続けてアンインストールできます。');
  };
  const updateAfterUninstall = (row) => {
    row.querySelectorAll('button[data-action]').forEach((button) => { button.disabled = true; });
    const enabled = row.querySelector('[data-enabled]');
    if (enabled) enabled.textContent = 'アンインストール済み';
    setFeedback(row, 'アンインストールが完了しました。状態を再確認するためアプリを再起動してください。');
    const restart = document.querySelector('[data-restart-note]');
    if (restart) restart.hidden = false;
  };
  document.querySelectorAll('button[data-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      const row = button.closest('tr');
      const packId = button.dataset.packId || '';
      const action = button.dataset.action;
      if (!row || !packId || !action || button.disabled) return;
      if (action === 'uninstall') {
        const packName = button.dataset.packName || '(名称不明)';
        const version = button.dataset.packVersion || '(不明)';
        const message = `この商品Packをアンインストールします。\n\n${packName}\nPack ID: ${packId}\nVersion: ${version}\n\n商品データは削除されます。過去の購入履歴は削除されません。\ncanonical Packの再導入は、再起動後の通常店長モードで行ってください。`;
        if (!globalThis.confirm(message)) return;
      }
      row.querySelectorAll('button[data-action]').forEach((b) => { b.disabled = true; });
      setFeedback(row, '処理中です…');
      try {
        const api = globalThis.pywebview && globalThis.pywebview.api;
        if (!api) throw new Error('bridge unavailable');
        const response = action === 'disable'
          ? await api.disable_pack(packId)
          : await api.uninstall_pack(packId);
        if (!response || response.ok !== true) {
          const message = response && response.error && response.error.message
            ? response.error.message
            : '保守操作を安全に完了できませんでした。';
          setFeedback(row, message);
          if (action === 'disable') button.disabled = false;
          if (action === 'uninstall') button.disabled = false;
          return;
        }
        if (action === 'disable') updateAfterDisable(row);
        else updateAfterUninstall(row);
      } catch (_) {
        setFeedback(row, '保守操作を安全に完了できませんでした。');
        button.disabled = false;
      }
    });
  });
})();
""".strip()


def _csp_hash(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def render_store_manager_recovery_html(rows: Iterable[RecoveryDisplayRow]) -> str:
    safe_rows = list(rows)
    rendered_rows: list[str] = []
    for index, row in enumerate(safe_rows):
        level_label = "安全な保守操作が可能" if row.safety_level == "SAFE_MAINTENANCE" else "状態確認のみ"
        level_class = "status-safe" if row.safety_level == "SAFE_MAINTENANCE" else "status-diagnostic"
        enabled_text = "有効" if row.enabled is True else "無効" if row.enabled is False else "不明"
        pack_id_attr = escape(row.pack_id, quote=True)
        pack_name_attr = escape(row.pack_name or "(名称不明)", quote=True)
        pack_version_attr = escape(row.pack_version or "(不明)", quote=True)
        disable_attrs = "" if row.can_disable else " disabled"
        uninstall_attrs = "" if row.can_uninstall else " disabled"
        if row.safety_level == "SAFE_MAINTENANCE":
            initial_feedback = (
                "有効なPackは先に無効化してください。"
                if row.enabled is True
                else "無効化済みです。正式Uninstall Lifecycleで退役できます。"
            )
            actions = (
                '<div class="actions">'
                f'<button class="primary" type="button" data-action="disable" data-pack-id="{pack_id_attr}"{disable_attrs}>Packを無効化</button>'
                f'<button class="danger" type="button" data-action="uninstall" data-pack-id="{pack_id_attr}" data-pack-name="{pack_name_attr}" data-pack-version="{pack_version_attr}"{uninstall_attrs}>Packをアンインストール</button>'
                f'<span class="feedback" data-feedback>{escape(initial_feedback)}</span>'
                '</div>'
            )
        else:
            actions = '<div class="actions"><span class="feedback" data-feedback>安全性を確定できないため操作できません。</span></div>'
        rendered_rows.append(
            f'<tr data-row="{index}">'
            f'<td>{escape(row.category)}</td>'
            f'<td>{escape(row.pack_name or "-")}<br><code>{escape(row.pack_id)}</code></td>'
            f'<td>{escape(row.pack_version or "-")}</td>'
            f'<td>{escape(row.operation_id or "-")}</td>'
            f'<td>{escape(row.operation_type or "-")}</td>'
            f'<td>{escape(row.state)}<br><span data-enabled>{enabled_text}</span></td>'
            f'<td><span class="{level_class}">{level_label}</span><br>{escape(row.maintenance_reason)}</td>'
            f'<td>{escape(row.message)}</td>'
            f'<td>{actions}</td>'
            '</tr>'
        )
    table_rows = "".join(rendered_rows)
    if not table_rows:
        table_rows = '<tr><td colspan="9">復旧対象の詳細を取得できませんでした。ログを確認してください。</td></tr>'

    style_hash = _csp_hash(_RECOVERY_STYLE)
    script_hash = _csp_hash(_RECOVERY_SCRIPT)
    csp = (
        "default-src 'none'; "
        f"script-src 'sha256-{script_hash}'; "
        f"style-src 'sha256-{style_hash}'; "
        "img-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>{APP_TITLE}</title>
<style>{_RECOVERY_STYLE}</style>
</head>
<body>
<main>
<h1>店長モード（復旧）</h1>
<div class="notice">
<p><strong>商品Packの状態確認が必要です。</strong></p>
<p>店舗機能は安全のため停止しています。商品販売・カート変更・架空購入は利用できません。</p>
<p>この画面は不整合を無視する場所ではありません。観測上安全な場合だけ、Legacy Packを退役させる最小保守操作を利用できます。</p>
</div>
<h2>確認が必要な状態</h2>
<div class="table-wrap">
<table>
<thead><tr><th>分類</th><th>商品Pack</th><th>Version</th><th>Operation ID</th><th>操作種別</th><th>状態</th><th>保守可否</th><th>案内</th><th>操作</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>
</div>
<p class="restart" data-restart-note hidden>状態が改善しました。通常bootstrapで再確認するため、アプリを終了して再起動してください。</p>
<p>Legacy Versionは自動変換しません。安全な場合は無効化し、正式Uninstall Lifecycleで退役させた後、再起動してcanonical Packを通常店長モードから導入してください。</p>
<p>技術ログ: %LOCALAPPDATA%\\FantasyStore\\logs</p>
</main>
<script>{_RECOVERY_SCRIPT}</script>
</body>
</html>"""


class StoreManagerRecoveryIntegration:
    """Restricted pywebview surface for Pack-subsystem safety failures."""

    def __init__(
        self,
        html: str,
        *,
        api: StoreManagerRecoveryApi,
        logger=None,
        webview_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.html = html
        self.api = api
        self.logger = logger
        self._webview_provider = webview_provider or (lambda: importlib.import_module("webview"))
        self._window = None

    @property
    def window(self) -> Any | None:
        return self._window

    def run(self):
        webview = self._webview_provider()
        settings = getattr(webview, "settings", None)
        if settings is not None:
            for key, value in (
                ("ALLOW_FILE_URLS", False),
                ("OPEN_EXTERNAL_LINKS_IN_BROWSER", False),
                ("OPEN_DEVTOOLS_IN_DEBUG", False),
                ("REMOTE_DEBUGGING_PORT", None),
            ):
                try:
                    settings[key] = value
                except Exception:
                    pass
        self._window = webview.create_window(
            APP_TITLE,
            html=self.html,
            js_api=self.api,
            width=1180,
            height=760,
            min_size=(820, 580),
            resizable=True,
            text_select=True,
        )
        if self._window is None:
            raise RuntimeError("pywebview did not create a recovery window")
        if self.logger:
            self.logger.warning("store-manager recovery UI started", extra={"event_code": "STORE_MANAGER_RECOVERY_UI"})
        webview.start(
            gui="edgechromium" if os.name == "nt" else None,
            debug=False,
            private_mode=True,
        )
        return True
