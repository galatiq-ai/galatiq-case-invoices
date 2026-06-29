import argparse

from orchestration.invoice_workflow import build_invoice_workflow


def run_workflow(invoice_path: str):
    workflow = build_invoice_workflow()

    result = workflow.invoke({
        "invoice_path": invoice_path,
        "invoice": None,
        "validation_result": None,
        "approval_result": None,
        "payment_result": None,
    })

    print("\n========== INGESTION ==========")
    print(result["invoice"].model_dump_json(indent=2))

    print("\n========== VALIDATION ==========")
    print(result["validation_result"].model_dump_json(indent=2))

    if result.get("approval_result"):
        print("\n========== APPROVAL ==========")
        print(result["approval_result"].model_dump_json(indent=2))
    else:
        print("\n========== APPROVAL ==========")
        print("Skipped because validation failed.")

    if result.get("payment_result"):
        print("\n========== PAYMENT ==========")
        print(result["payment_result"].model_dump_json(indent=2))
    else:
        print("\n========== PAYMENT ==========")
        print("Skipped because validation failed.")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-agent invoice processing automation system"
    )

    parser.add_argument(
        "--invoice_path",
        required=True,
        help="Path to the invoice file"
    )

    args = parser.parse_args()
    run_workflow(args.invoice_path)


if __name__ == "__main__":
    main()