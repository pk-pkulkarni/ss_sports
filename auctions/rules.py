from __future__ import annotations

from typing import Any


AUCTION_TYPE_OPEN = "open"
AUCTION_TYPE_RULE_BASED = "rule_based"

RULE_LOT1 = "lot1_must_sell_first"
RULE_LOT1_AND_LOT2 = "lot1_and_lot2_must_sell"
RULE_PRICE_CAPS = "price_caps"

PRICE_CAP_VALUES = (700000, 500000, 400000)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def default_auction_rules() -> dict[str, Any]:
    return {
        "auction_type": AUCTION_TYPE_OPEN,
        "is_rule_based": False,
        "selected_rules": [],
        "rule_lot1_enabled": False,
        "rule_lot1_and_lot2_enabled": False,
        "rule_price_caps_enabled": False,
        "lot1_players_per_team": None,
        "lot2_players_per_team": None,
        "price_cap_values": list(PRICE_CAP_VALUES),
        "price_cap_limit": 1,
    }


def normalize_auction_rules(auction) -> dict[str, Any]:
    raw = auction.rules if getattr(auction, "rules", None) and isinstance(auction.rules, dict) else {}
    config = default_auction_rules()

    auction_type = raw.get("auction_type")
    if auction_type in {AUCTION_TYPE_OPEN, AUCTION_TYPE_RULE_BASED}:
        config["auction_type"] = auction_type

    config["lot1_players_per_team"] = _positive_int(raw.get("lot1_players_per_team"))
    config["lot2_players_per_team"] = _positive_int(raw.get("lot2_players_per_team"))

    config["rule_lot1_enabled"] = bool(raw.get(RULE_LOT1))
    config["rule_lot1_and_lot2_enabled"] = bool(raw.get(RULE_LOT1_AND_LOT2))
    config["rule_price_caps_enabled"] = bool(raw.get(RULE_PRICE_CAPS))

    if config["rule_lot1_and_lot2_enabled"]:
        config["rule_lot1_enabled"] = False

    if config["auction_type"] != AUCTION_TYPE_RULE_BASED:
        return config

    selected_rules: list[str] = []
    if config["rule_lot1_enabled"]:
        selected_rules.append(RULE_LOT1)
    if config["rule_lot1_and_lot2_enabled"]:
        selected_rules.append(RULE_LOT1_AND_LOT2)
    if config["rule_price_caps_enabled"]:
        selected_rules.append(RULE_PRICE_CAPS)

    config["selected_rules"] = selected_rules
    config["is_rule_based"] = bool(selected_rules)
    if not selected_rules:
        config["auction_type"] = AUCTION_TYPE_OPEN

    return config
