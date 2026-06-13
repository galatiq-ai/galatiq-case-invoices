# Galatiq Invoice Processing Solution Overview

This document explains the solution at three levels:

1. High-level logic of the system
2. Use-case-by-use-case explanation of how the system behaves
3. Salient features that make the solution useful and reviewable

The implementation is a local, multi-agent invoice processing pipeline built
with LangGraph, SQLite, and a small Streamlit UI. It is designed to automate
the invoice flow from file ingestion through validation, approval, and mock
payment, while keeping the results observable and auditable.

## 1. High-Level Logic

The system treats invoice processing as a staged workflow.

1. An invoice file is loaded from disk.
2. The file is converted into structured invoice data.
3. The extracted data is validated against local inventory records.
4. A rule-based approval decision is made, with an optional critique pass.
5. Approved invoices trigger a mock payment action.
6. Every stage writes trace information so the result can be reviewed later.

The main design idea is:

- keep the pipeline deterministic where possible
- use LLM reasoning only where it adds value
- preserve human-readable reasons for each decision
- keep the whole solution runnable locally

### Core Runtime Flow

The workflow is implemented as a LangGraph state machine.

1. `ingest` stage
   - reads the invoice file
   - extracts invoice number, vendor, items, totals, dates, and currency
   - supports TXT, CSV, JSON, XML, and PDF files

2. `validate` stage
   - checks extracted line items against the SQLite inventory database
   - flags unknown items, stock mismatches, zero-stock items, and data issues

3. `approve` stage
   - applies policy rules such as high-value scrutiny
   - checks for duplicates, suspicious language, known vendor status, and currency
   - can send the decision through a critique pass

4. `payment` stage
   - if approved, runs a mock payment
   - if rejected or held, records the skip outcome

The final result is returned as a structured state object and can be rendered
in the CLI or UI.

## 2. Use Cases And How The Solution Handles Them

### Use Case 1: Clean Invoice Within Stock

**Problem**

A normal invoice arrives with valid items, known vendor, and quantities that
are available in inventory.

**Solution Behavior**

1. Ingestion extracts the invoice fields and line items.
2. Validation checks all items against inventory and finds no mismatch.
3. Approval sees a clean invoice and approves it.
4. Payment executes the mock payment.
5. Trace and audit records show the entire chain.

**Outcome**

The invoice is processed end to end with minimal friction.

**Why this matters**

This is the happy path. It demonstrates the system can automate routine AP
work without manual intervention.

---

### Use Case 2: Quantity Exceeds Available Stock

**Problem**

An invoice requests more units than exist in inventory.

**Solution Behavior**

1. Ingestion extracts the line item quantities.
2. Validation compares requested quantity to available stock.
3. The line item is flagged as `stock_mismatch`.
4. Approval moves the invoice to `hold` and adds a reason.
5. Payment is skipped.

**Outcome**

The invoice does not get paid automatically. It is surfaced for review.

**Why this matters**

This prevents overpayment and highlights potential operational or vendor issues.

---

### Use Case 3: Zero-Stock Or Fraud-Suspicious Item

**Problem**

The invoice references a stock-0 item such as `FakeItem`.

**Solution Behavior**

1. Validation marks the item as out of stock.
2. Approval applies a hard rejection rule.
3. The reason explicitly indicates a suspicious or fraudulent entry.
4. Payment is skipped and the invoice is marked rejected.

**Outcome**

The system blocks the invoice instead of allowing manual payment.

**Why this matters**

This is a strong fraud-control path and matches the case study expectation of
catching suspicious invoices early.

---

### Use Case 4: Unknown Item Not In Inventory

**Problem**

The invoice contains product names that are not present in the inventory DB.

**Solution Behavior**

1. Ingestion normalizes item names as best it can.
2. Validation cannot find the item in inventory and flags it as `unknown_item`.
3. Approval places the invoice on hold and asks for procurement or vendor review.
4. Payment is skipped.

**Outcome**

The invoice is not silently accepted; it is routed to review.

**Why this matters**

Unknown products are common in messy invoice flows, and they should not pass
through as if they were known stock.

---

### Use Case 5: Negative Quantity Or Invalid Data

**Problem**

The invoice has a negative quantity, negative total, or malformed core data.

**Solution Behavior**

1. Validation flags the data integrity issue.
2. Approval applies a hard rejection rule.
3. The reason clearly states that the invoice contains invalid data.
4. Payment is skipped.

**Outcome**

The invoice is rejected with a strong explanation.

**Why this matters**

This protects downstream payment processing from clearly invalid input.

---

### Use Case 6: High-Value Invoice Over $10K

**Problem**

The invoice amount exceeds the business threshold for automatic handling.

**Solution Behavior**

1. Approval adds an additional scrutiny requirement.
2. The invoice can be approved, held, or critiqued based on the combined rules.
3. The reasoning explains that the amount crossed the high-value threshold.
4. Payment only happens if the invoice ends up approved.

**Outcome**

High-value invoices receive extra attention rather than being auto-processed.

**Why this matters**

This mirrors real finance controls where larger invoices need extra oversight.

---

### Use Case 7: Duplicate Invoice Submission

**Problem**

The same invoice number is sent again, or the invoice appears to be a repeat.

**Solution Behavior**

1. The approval stage checks whether the invoice has already been processed.
2. If a previous status exists, the invoice is treated as a potential duplicate.
3. The decision is moved to hold.
4. Payment is skipped and the reason is recorded.

**Outcome**

The system reduces the risk of double payment.

**Why this matters**

Duplicate payment prevention is a major business value driver in AP automation.

---

### Use Case 8: Suspicious Urgency Language

**Problem**

The invoice contains language like "pay immediately" or "avoid penalties."

**Solution Behavior**

1. Ingestion reduces confidence when urgency language appears.
2. Approval treats the invoice as suspicious and adds fraud-review reasoning.
3. Depending on the rest of the signals, the invoice can be held or rejected.

**Outcome**

Suspicious invoices are not treated like normal transactions.

**Why this matters**

Fraudulent invoices often try to create pressure and bypass review.

---

### Use Case 9: PDF, CSV, JSON, XML, And Text Inputs

**Problem**

Invoices arrive in inconsistent formats.

**Solution Behavior**

1. The reader detects file type by extension.
2. PDFs are read via text extraction, with fallback handling.
3. JSON, CSV, XML, and TXT are parsed with format-aware logic.
4. The rest of the pipeline works on normalized structured state.

**Outcome**

The same business flow can handle multiple input formats.

**Why this matters**

Real invoice operations rarely standardize on a single file format.

## 3. Salient Features

### 1. Multi-Agent Workflow

The solution is divided into clear stages:

- ingestion
- validation
- approval
- payment

This makes the system easier to reason about and test.

### 2. Deterministic First, LLM When Needed

The code favors deterministic parsing and policy logic first, then uses LLM
reasoning as a fallback or critique layer where appropriate.

This gives a good balance of:

- reliability
- explainability
- flexibility for messy inputs

### 3. Strong Validation Layer

The validation step checks:

- item existence
- stock availability
- quantity mismatches
- invalid totals or quantities
- missing core metadata

This keeps bad invoices from moving forward unnoticed.

### 4. Rule-Based Approval With Critique

The approval stage does not just return a yes/no answer.
It reasons about:

- high-value invoices
- duplicates
- known vendors
- suspicious wording
- validation failures

It also supports a critique loop so the decision can be refined.

### 5. Mock Payment With Clear Outcomes

Approved invoices trigger a mock payment and produce a transaction ID.
Rejected or held invoices are recorded as skipped, with a clear reason.

### 6. Traceability And Auditability

The solution writes:

- per-invoice trace files
- SQLite audit rows
- log output for each stage

This makes the system easier to troubleshoot and review.

### 7. User-Friendly UI Summary

The Streamlit UI now presents:

- invoice header information
- outcome cards
- a processing timeline
- plain-language next actions
- raw JSON only in an expandable technical section

This is much more usable for non-technical reviewers.

### 8. Local-First Execution

The system runs locally, with no cloud deployment required.
It can also fall back to deterministic behavior when LLM keys are absent.

### 9. Batch Processing And Export

The CLI supports batch invoice processing and CSV output for quick review.

### 10. Extensible Code Structure

The code is organized into separate modules for:

- agents
- orchestration
- persistence
- tools
- UI
- observability

That keeps the system understandable and easier to extend.

## 4. Summary

This solution automates the invoice workflow by combining:

- format-aware ingestion
- inventory validation
- policy-based approval
- mock payment execution
- trace and audit logging

It is especially strong in situations where invoices are messy, duplicated,
or need additional scrutiny. The system keeps business outcomes clear while
still preserving the technical details needed for auditing and debugging.

