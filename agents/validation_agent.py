from models.invoice_models import (
    Invoice,
    ValidationIssue,
    ValidationResult,
    IssueType,
)
from tools.inventory_tool import InventoryTool


class ValidationAgent:
    """
    Validates invoice items against the inventory database.
    """

    def __init__(self):
        self.inventory_tool = InventoryTool()

    def validate(self, invoice: Invoice) -> ValidationResult:
        issues = []

        for item in invoice.items:

            stock = self.inventory_tool.get_stock(item.name)

            if stock is None:
                issues.append(
                    ValidationIssue(
                        item_name=item.name,
                        issue_type=IssueType.UNKNOWN_ITEM,
                        message=f"{item.name} does not exist in inventory."
                    )
                )
                continue

            if item.quantity <= 0:
                issues.append(
                    ValidationIssue(
                        item_name=item.name,
                        issue_type=IssueType.INVALID_QUANTITY,
                        message=f"Invalid quantity: {item.quantity}"
                    )
                )
                continue

            if stock == 0:
                issues.append(
                    ValidationIssue(
                        item_name=item.name,
                        issue_type=IssueType.OUT_OF_STOCK,
                        message=f"{item.name} is out of stock."
                    )
                )
                continue

            if item.quantity > stock:
                issues.append(
                    ValidationIssue(
                        item_name=item.name,
                        issue_type=IssueType.STOCK_MISMATCH,
                        message=(
                            f"Requested {item.quantity}, "
                            f"but only {stock} available."
                        )
                    )
                )

        return ValidationResult(
            passed=len(issues) == 0,
            issues=issues
        )