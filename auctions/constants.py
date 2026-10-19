JERSEY_SIZE_DETAILS = {
    "XS": {"label": "XS", "number": 34},
    "S": {"label": "S", "number": 36},
    "M": {"label": "M", "number": 38},
    "L": {"label": "L", "number": 40},
    "XL": {"label": "XL", "number": 42},
    # Keep the existing database value for backward compatibility while
    # presenting the size in the requested 2XL format.
    "XXL": {"label": "2XL", "number": 44},
    "3XL": {"label": "3XL", "number": 46},
    "4XL": {"label": "4XL", "number": 48},
}

JERSEY_SIZE_CHOICE_LABELS = {
    value: f"{details['label']} - {details['number']}"
    for value, details in JERSEY_SIZE_DETAILS.items()
}


def jersey_size_label(value: str | None) -> str:
    """Return the user-facing letter size, including the XXL -> 2XL alias."""
    code = str(value or "").strip().upper()
    if code == "2XL":
        code = "XXL"
    details = JERSEY_SIZE_DETAILS.get(code)
    return details["label"] if details else code


def jersey_size_number(value: str | None) -> int | None:
    """Map a stored jersey size to its numeric chest-size equivalent."""
    code = str(value or "").strip().upper()
    if code == "2XL":
        code = "XXL"
    details = JERSEY_SIZE_DETAILS.get(code)
    return details["number"] if details else None
