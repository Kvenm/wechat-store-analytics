from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
ORDER_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:\d{12,}|[A-Za-z]{1,8}\d{8,}[A-Za-z0-9]*)(?![A-Za-z0-9])")
ADDRESS_PATTERN = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9]{2,}"
    r"(?:省|市|区|县|镇|乡|街道|路|街|巷|号楼|单元|室|村|社区|小区|大厦|广场)"
    r"[\u4e00-\u9fa5A-Za-z0-9\-#（）()]{0,40}"
)


DEFAULT_NICKNAME_KEYS = {
    "buyer",
    "buyer_id",
    "buyer_name",
    "customer",
    "customer_id",
    "customer_name",
    "nickname",
    "nick_name",
    "openid",
    "user_id",
    "user_name",
}
DEFAULT_ADDRESS_KEYS = {
    "address",
    "buyer_address",
    "city_detail",
    "detail_address",
    "receiver_address",
    "shipping_address",
}
DEFAULT_PHONE_KEYS = {
    "buyer_phone",
    "contact_phone",
    "mobile",
    "phone",
    "receiver_phone",
    "tel",
}
DEFAULT_ORDER_KEYS = {
    "order_id",
    "order_no",
    "order_number",
    "transaction_id",
}


@dataclass(frozen=True)
class PrivacyOptions:
    mask_phone: bool = True
    mask_address: bool = True
    mask_nickname: bool = True
    mask_order_id: bool = True
    nickname_keys: set[str] = field(default_factory=lambda: set(DEFAULT_NICKNAME_KEYS))
    address_keys: set[str] = field(default_factory=lambda: set(DEFAULT_ADDRESS_KEYS))
    phone_keys: set[str] = field(default_factory=lambda: set(DEFAULT_PHONE_KEYS))
    order_id_keys: set[str] = field(default_factory=lambda: set(DEFAULT_ORDER_KEYS))
    replacement: str = "[REDACTED]"


def mask_text(value: Any, options: PrivacyOptions | None = None) -> Any:
    """Mask sensitive tokens in free text while preserving non-text values."""
    if not isinstance(value, str):
        return value
    opts = options or PrivacyOptions()
    text = value
    if opts.mask_phone:
        text = PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
    if opts.mask_order_id:
        text = ORDER_ID_PATTERN.sub("[ORDER_REDACTED]", text)
    if opts.mask_address:
        text = ADDRESS_PATTERN.sub("[ADDRESS_REDACTED]", text)
    return text


def redact_record(record: dict[str, Any], options: PrivacyOptions | None = None) -> dict[str, Any]:
    """Redact a single mapping by sensitive key names and text patterns."""
    opts = options or PrivacyOptions()
    redacted: dict[str, Any] = {}
    for key, value in record.items():
        key_lower = str(key).lower()
        if opts.mask_phone and key_lower in opts.phone_keys:
            redacted[key] = "[PHONE_REDACTED]"
        elif opts.mask_address and key_lower in opts.address_keys:
            redacted[key] = "[ADDRESS_REDACTED]"
        elif opts.mask_nickname and key_lower in opts.nickname_keys:
            redacted[key] = "[USER_REDACTED]"
        elif opts.mask_order_id and key_lower in opts.order_id_keys:
            redacted[key] = "[ORDER_REDACTED]"
        else:
            redacted[key] = redact_payload(value, opts)
    return redacted


def redact_payload(payload: Any, options: PrivacyOptions | None = None) -> Any:
    """Recursively redact dict/list payloads before they are sent to AI tooling."""
    opts = options or PrivacyOptions()
    if isinstance(payload, dict):
        return redact_record(payload, opts)
    if isinstance(payload, list):
        return [redact_payload(item, opts) for item in payload]
    return mask_text(payload, opts)
