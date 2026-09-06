from __future__ import annotations

import logging
from typing import Any, Callable

from fantasy_store.domain.errors import DomainError


_SAFE_MESSAGES: dict[str, str] = {
    "VALIDATION_INVALID_ARGUMENT": "入力内容を確認してください。",
    "PRODUCT_NOT_AVAILABLE": "この商品は現在利用できません。商品一覧を再読み込みしてください。",
    "CART_QUANTITY_LIMIT": "数量は1〜999の範囲で指定してください。",
    "CHECKOUT_ITEM_UNAVAILABLE": "カート内に現在購入できない商品があります。内容を確認してください。",
    "ORDER_NOT_FOUND": "指定された購入履歴が見つかりません。",
    "PACK_BUSY": "この商品パックは更新処理中です。少し待ってから再試行してください。",
    "PACK_FILE_LOCKED": "商品パックのファイルを使用中のため処理できません。少し待ってから再試行してください。",
    "PACK_NOT_FOUND": "指定された商品パックが見つかりません。",
    "PACK_UNINSTALL_REQUIRES_DISABLED": "アンインストールするには、先にこの商品パックを無効にしてください。",
    "PACK_VALIDATION_FAILED": "コンテンツパックの形式または内容が正しくありません。",
    "PACK_OPERATION_FAILED": "商品パックの処理に失敗しました。",
    "DB_OPEN_FAILED": "データベースを開けませんでした。",
    "DB_SCHEMA_CORRUPT": "データベースの構造に問題があります。",
    "DB_VERSION_UNSUPPORTED": "このデータは現在のアプリでは読み込めません。",
    "DB_WRITE_FAILED": "データの保存に失敗しました。",
    "DB_BACKUP_FAILED": "バックアップの作成に失敗しました。",
    "DB_RECOVERY_FAILED": "利用者データを安全に復旧できませんでした。",
    "DB_MIGRATION_FAILED": "データ更新処理に失敗しました。",
    "FS_ACCESS_DENIED": "ファイルへアクセスできません。権限や使用中の状態を確認してください。",
    "FS_DISK_FULL": "保存先の空き容量が不足しています。",
    "RECOVERY_REQUIRED": "商品パックの状態を安全に確定できません。通常操作を停止しました。店長モードで状態を確認してください。",
    "RUNTIME_ALREADY_RUNNING": "このアプリはすでに起動しています。",
}


def success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def failure(code: str, message: str) -> dict[str, Any]:
    # details is part of the frozen detailed-design envelope and is deliberately
    # kept null so no technical exception data crosses the Bridge boundary.
    return {"ok": False, "data": None, "error": {"code": code, "message": message, "details": None}}


def safe_message_for(code: str) -> str:
    return _SAFE_MESSAGES.get(code, "処理に失敗しました。アプリのログを確認してください。")


def bridge_guard(logger: logging.Logger, action: str, fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return success(fn())
    except DomainError as exc:
        logger.warning(
            "bridge domain error in %s: %s",
            action,
            exc.code,
            extra={"event_code": "BRIDGE_DOMAIN_ERROR"},
        )
        return failure(exc.code, safe_message_for(exc.code))
    except Exception:
        logger.exception("unexpected bridge exception in %s", action, extra={"event_code": "BRIDGE_INTERNAL_ERROR"})
        return failure("INTERNAL_ERROR", "予期しないエラーが発生しました。アプリのログを確認してください。")
