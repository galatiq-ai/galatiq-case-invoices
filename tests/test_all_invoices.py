from pathlib import Path
from openpyxl import Workbook

from orchestration.invoice_workflow import build_invoice_workflow

workflow = build_invoice_workflow()

invoice_dir = Path("data/invoices")

wb = Workbook()
ws = wb.active
ws.title = "Invoice Results"

ws.append([
    "Invoice File",
    "Invoice Number",
    "Vendor",
    "Validation",
    "Issues",
    "Approval",
    "Payment",
    "Status"
])

for invoice_path in sorted(invoice_dir.iterdir()):
    if invoice_path.suffix.lower() not in [".txt", ".json", ".csv", ".xml", ".pdf"]:
        continue

    try:
        result = workflow.invoke({
            "invoice_path": str(invoice_path),
            "invoice": None,
            "validation_result": None,
            "approval_result": None,
            "payment_result": None,
        })

        invoice = result["invoice"]
        validation = result["validation_result"]
        approval = result.get("approval_result")
        payment = result.get("payment_result")

        issues = "; ".join(
            f"{issue.issue_type}: {issue.message}"
            for issue in validation.issues
        )

        ws.append([
            invoice_path.name,
            invoice.invoice_number,
            invoice.vendor,
            validation.passed,
            issues,
            approval.status if approval else "SKIPPED",
            payment.status if payment else "SKIPPED",
            "SUCCESS"
        ])

    except Exception as e:
        ws.append([
            invoice_path.name,
            "",
            "",
            "",
            "",
            "",
            "",
            f"FAILED - {str(e)}"
        ])

output_file = "invoice_test_results.xlsx"
wb.save(output_file)

print(f"\nResults saved to {output_file}")