from invoice_pipeline.approval import HEIGHTENED_SCRUTINY_USD
from invoice_pipeline.catalog import Catalog
from invoice_pipeline.model import (
    Agents,
    ArrivalSummary,
    CaseFile,
    Finding,
    HistoryEntry,
    Invoice,
    References,
    RoleCall,
    UsdEquivalent,
)
from invoice_pipeline.validation import PRICE_TOLERANCE, aggregate_quantities, price_deviations

OFFLINE_TIER = "offline tier"


def build_case_file(
    invoice: Invoice,
    findings: list[Finding],
    arrival: ArrivalSummary,
    usd: UsdEquivalent | None,
    catalog: Catalog,
    history: list[HistoryEntry],
    history_total: int,
) -> CaseFile:
    """Assemble everything a model role may read. Pure: no I/O, no model arithmetic."""
    skus = {s for item in invoice.items if (s := catalog.resolve_sku(item.sku)) is not None}
    references = References(
        reference_prices={s: catalog.prices[s] for s in skus if s in catalog.prices},
        stock_levels={s: catalog.stock[s] for s in skus if s in catalog.stock},
        aggregated_quantities=aggregate_quantities(invoice, catalog),
        price_tolerance=PRICE_TOLERANCE,
        price_deviations=price_deviations(invoice, catalog),
        usd_equivalent=usd,
        heightened_scrutiny_line=HEIGHTENED_SCRUTINY_USD,
    )
    return CaseFile(
        invoice=invoice,
        findings=findings,
        arrival=arrival,
        references=references,
        vendor_history=history,
        vendor_history_total=history_total,
    )


def offline_role(role: str) -> RoleCall:
    """An absent single-call role, recorded as the offline tier (no request is made)."""
    return RoleCall(
        role=role, tier="offline", model=None, tries=[], answer=None, error=OFFLINE_TIER
    )


def offline_agents() -> Agents:
    """The permanent `Agents` bundle for the offline tier; every path fails closed, no I/O."""
    return Agents(
        escalate_review=lambda case_file: offline_role("escalate_review"),
        advise=lambda case_file, decision: offline_role("advisory"),
    )
