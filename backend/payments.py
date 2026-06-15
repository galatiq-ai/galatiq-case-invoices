"""Mock payment rail — stands in for Acme's banking API.

No network: a disbursement is logged and returned with a reference, so the
pipeline has a real, recorded terminal action without leaving the machine. Only
the pay node calls this, and only after the gate has cleared the invoice.
"""

import logging
import uuid

log = logging.getLogger("payments")


def pay(vendor: str, amount: float, currency: str) -> dict:
    reference = f"pay_{uuid.uuid4().hex[:12]}"
    log.info("PAYMENT %s: %.2f %s -> %s", reference, amount, currency, vendor)
    return {"status": "success", "reference": reference,
            "vendor": vendor, "amount": amount, "currency": currency}
