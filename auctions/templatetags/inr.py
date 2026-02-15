from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _format_indian_commas(integer_str: str) -> str:
    """Format the integer part using Indian numbering system (lakhs/crores)."""
    if len(integer_str) <= 3:
        return integer_str

    last_three = integer_str[-3:]
    rest = integer_str[:-3]

    parts = []
    while rest:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]

    return ",".join(parts + [last_three])


@register.filter(name="inr")
def inr(value, precision=0):
    """
    Format numbers with Indian digit grouping. Optional precision argument (default: 0).
    Usage:
      {{ amount|inr }}          -> 1,23,456
      {{ amount|inr:2 }}        -> 1,23,456.78
    """
    if value is None:
        return ""

    try:
        precision = int(precision)
    except (TypeError, ValueError):
        precision = 0

    precision = max(0, precision)

    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    quantize_exp = Decimal(10) ** -precision
    try:
        num = num.quantize(quantize_exp)
    except InvalidOperation:
        pass  # fallback to raw num if quantize fails

    sign = "-" if num < 0 else ""
    num_abs = abs(num)

    formatted = f"{num_abs:.{precision}f}"
    integer_str, _, fractional_str = formatted.partition(".")

    grouped = _format_indian_commas(integer_str)

    if precision > 0:
        return f"{sign}{grouped}.{fractional_str}"
    return f"{sign}{grouped}"
