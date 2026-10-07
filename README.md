# Galatiq case: Invoice processing automation

## Background

Acme Corp is a manufacturing firm backed by private equity. It loses **$2,000,000 a year** on manual invoice processing. Invoices arrive by email, vendor portal and scan, in messy formats with frequent errors, and some are fraudulent. Staff extract the data by hand, check it against a legacy ERP (inconsistent), chase VP approval over email chains, and pay through a banking API.

**Current pain points:**
- 30% error rate
- 5 day processing delays
- Duplicate and fraudulent payments found weeks later, if at all
- Frustrated stakeholders

## Objective

Build a **multi agent system** that automates Acme's invoice workflow from intake to payment. The system must run as a working prototype (designs or slides alone don't count) and make the same call Acme's AP team would make under its policy.

Plan on about 6 hours of focused work. There's more here than fits in 6 hours. Deciding what to build first is part of the exercise, so explain what you cut and why. Use AI coding tools as much as you like.

## Workflow

The system should handle four stages:

| Stage | What the system does |
|---|---|
| 1. Ingestion | Extract structured data from each document: vendor, invoice number, dates, line items with quantities and prices, totals, currency, PO reference and remittance details. Documents arrive as text, PDF (including scans), CSV, JSON, XML and email. Expect typos, OCR noise, other languages, missing data, and content written to mislead. |
| 2. Validation | Check each invoice against Acme's ERP (vendor master, purchase orders, goods receipts, payment history, inventory) as the AP policy requires. |
| 3. Approval | Apply the policy's approval matrix. Simulate VP review where the policy calls for it; the agent should reason through approval or rejection with a reflection or critique loop. Route anything the system isn't allowed to approve to the right person. |
| 4. Payment | Pay approved invoices through the mock payment function, and never pay the same invoice twice. Log every rejection and hold with reasoning a person can act on. |

## Technical requirements

| Requirement | Detail |
|---|---|
| LLM | Use SpaceXAI's Grok as the core reasoning engine (see LLM setup below). |
| Orchestration | Use a framework such as LangGraph, CrewAI or AutoGen, or build your own. |
| Agent capabilities | Function calling and tool use, structured outputs, and self correction loops. |
| Runtime | The LLM is the only network call. Simulate everything else locally. |
| Stack | Python (preferred), with libraries such as `langchain`, `crewai`, `autogen`, `pdfplumber` and `PyMuPDF`. Runs locally, with no cloud deployment. |

## Provided resources

### AP policy

[`data/policy/ap_policy.md`](data/policy/ap_policy.md) describes how Acme's AP team decides. It's the source of truth for every decision your system makes. Like most real policies, it isn't perfect. Where it's silent or ambiguous, make a call and write it down.

### Legacy ERP (SQLite)

[`data/erp/acme_erp.sql`](data/erp/acme_erp.sql) is an export of Acme's ERP: vendors, items, inventory, purchase orders and their lines, goods receipts, past payments and FX rates. The schema is documented at the top of the file. Build the database with:

```bash
python -c "import sqlite3; sqlite3.connect('acme_erp.db').executescript(open('data/erp/acme_erp.sql').read())"
```

Your system must read ERP data from the file passed with `--db`. Reviewers run it against a different file with the same schema and different records, so don't hardcode anything from this one.

### Sample documents

[`data/invoices/`](data/invoices/) holds 28 sample documents for development. Reviewers evaluate on a different set of documents. Identifying and handling problems is part of the challenge.

### Mock payment API

```python
def mock_payment(vendor, amount):
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}
```

### LLM setup

The Grok API uses the widely adopted chat completions format, so the `openai` Python package works with it. Read the endpoint, key and model from environment variables, because reviewers run your system with Galatiq's key:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("LLM_BASE_URL", "https://api.x.ai/v1"),
    api_key=os.environ["LLM_API_KEY"],
)
response = client.chat.completions.create(
    model=os.environ["LLM_MODEL"],
    messages=[{"role": "user", "content": "Reason about this..."}],
)
```

If you don't have a Grok key, other models are acceptable through the same variables. Evaluation runs on Grok.

## Running the system

The system should be executable from the command line:

```bash
# One document
python main.py --invoice_path data/invoices/invoice_1001.txt --db acme_erp.db --out results.jsonl

# A folder, processed in file name order, remembering what has already been processed and paid
python main.py --batch data/invoices --db acme_erp.db --out results.jsonl
```

For each document, write one JSON object as one line of `--out` (JSON Lines), following [`data/output_schema.json`](data/output_schema.json). Add any other fields you find useful. Send logs to stderr or a log file, not to `--out`.

It must run from a clean clone using the setup steps in your README. A system that doesn't run can't be scored.

## How submissions are evaluated

1. Reviewers run your system in batch mode on documents you haven't seen, against an ERP file you haven't seen, with Galatiq's key. Paying an invoice that shouldn't be paid is the most expensive mistake. Holding a clean invoice costs less, but a system that holds everything saves nobody any time.
2. Reviewers read your code, tests and README. Judgment counts for more than feature count: where you used the LLM and where you didn't, how you know the decisions are right, and how you handled gaps in the policy.
3. In the next interview, you add a new requirement to your own system, live, in about 30 minutes, using your usual tools.

### Evaluation criteria

| Criterion | What reviewers look for |
|---|---|
| Correct decisions | How often the system makes the right call on documents it hasn't seen. |
| Judgment | Sound reading of the policy and the data. Ambiguities found, decided and documented. |
| Agentic sophistication | LLM integration, multi agent flow, tool use and self correction loops, used where they help and robust to hostile input. |
| Evidence | How you know it works: tests, an evaluation you ran, the error rate you measured. |
| Code quality | Clean, testable, well structured code with error handling and observability. A reviewer can trace why any document got its decision. |
| Shipping mindset | A valuable MVP delivered under ambiguity, with scope cut deliberately. |
| Presentation | Technical decisions translated clearly into business impact. |
| UI/UX | An AP clerk understands why each invoice was held and what to do next. |
| Above and beyond | You made it your own. Extras count when they improve decisions or the AP team's day; volume alone earns nothing. |

## Your README

Keep it short; one page is fine. Include:

- Setup and run commands that work from a clean clone
- Results on the sample documents, and how you measured them
- A decision log: where the policy or data was ambiguous, and what you decided
- What you cut, and what you'd do before going to production
- Business impact: what this saves Acme, and what could go wrong
- Roughly how long you spent

## Submission

Submit your solution as a link to a public GitHub repository. GitHub only (github.com).
