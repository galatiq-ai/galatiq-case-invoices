"""Deterministic normalizers shared by every parser. Floats never touch money."""

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from invoice_pipeline.model import Repair

_CENT = Decimal("0.01")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_O_FOR_ZERO = re.compile(r"(?<=[\d.,])[Oo]|[Oo](?=\d)")  # the letter O read in a digit position
_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%b %d %Y", "%b %d, %Y", "%B %d %Y", "%B %d, %Y")


def clean_text(raw: str | None) -> str | None:
    """Trimmed text, or None when blank. Internal spacing is kept (display names)."""
    return (raw or "").strip() or None


def parse_money(raw, field: str, repairs: list[Repair]) -> Decimal | None:
    """Exact cents from "$3,500.O0"-style text; None when unparseable. Records any O repair."""
    if isinstance(raw, float):
        raise TypeError("money must be str, int or Decimal, never float")
    if isinstance(raw, int | Decimal):
        return Decimal(raw).quantize(_CENT, ROUND_HALF_UP)
    text = (raw or "").strip()
    cleaned = re.sub(r"[$€£,\s]", "", text)
    fixed = _O_FOR_ZERO.sub("0", cleaned)
    if not _NUMBER.fullmatch(fixed):
        return None
    value = Decimal(fixed).quantize(_CENT, ROUND_HALF_UP)
    if fixed != cleaned:
        repairs.append(Repair(field=field, raw=text, repaired=str(value)))
    return value


def parse_date(raw: str | None, field: str, repairs: list[Repair]) -> date | None:
    """A date in the supported spellings; None for text like "yesterday". Records any O repair."""
    text = (raw or "").strip()
    fixed = _O_FOR_ZERO.sub("0", text)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(fixed, fmt).date()
        except ValueError:
            continue
        if fixed != text:
            repairs.append(Repair(field=field, raw=text, repaired=fixed))
        return parsed
    return None


def parse_quantity(raw) -> Decimal | None:
    """A plain numeric token only; anything else stays unparsed (QUANTITY_INVALID downstream)."""
    if isinstance(raw, int | Decimal):
        return Decimal(raw)
    text = (raw or "").strip() if isinstance(raw, str) else ""
    return Decimal(text) if _NUMBER.fullmatch(text) else None


def normalize_sku(raw: str | None) -> tuple[str | None, str | None]:
    """(SKU, note): spaces removed, words capitalized, a parenthetical moved into the note."""
    text = raw or ""
    notes = [note.strip() for note in re.findall(r"\(([^)]*)\)", text) if note.strip()]
    words = re.sub(r"\([^)]*\)", " ", text).split()
    sku = "".join(word[:1].upper() + word[1:] for word in words) or None
    return sku, "; ".join(notes) or None
