# Acme Corp: Accounts payable policy

AP-POL-004, revision 7 · Owner: Controller's Office · Effective July 1, 2025 · Amended January 26, 2026 (see §10)

This policy says how Acme's Accounts Payable team decides whether to pay a supplier invoice. It applies to every invoice and credit memo AP receives, however it arrives.

---

## 1. Outcomes

Every document ends in exactly one outcome:

| Outcome | Meaning |
|---|---|
| Approve | All checks pass and the required approvals are obtained. The invoice is paid (credit memos are recorded, not paid; see §8). |
| Reject | Returned to the vendor, who must fix and resubmit it. Rejections must say what the vendor needs to fix. |
| Hold | Not paid yet. Routed to a named team for a human decision. Holds must say who needs to act and why. |

If more than one outcome applies, the stricter one wins: reject over hold, hold over approve. The one exception is possible fraud (§3.3, §7.4), which is always a hold, even when another rule calls for rejection, so that Internal Audit sees it.

## 2. Intake

2.1 Invoices arrive by email (in the message body or as an attachment), by vendor portal upload (CSV, JSON, XML), or as scans. The same invoice can arrive through more than one channel.

2.2 Every invoice must show the vendor name, invoice number, invoice date, at least one line item (description, quantity, unit price) and a total. If any of these is missing, reject. If the invoice doesn't state a currency, use the vendor's currency from the vendor master. The due date isn't required (see §9.4).

2.3 The invoice must add up. Each line amount must equal quantity × unit price, the subtotal must equal the sum of the lines, and the total must equal subtotal + tax + freight. Where a tax rate is printed, the tax amount must match it. A difference of up to $0.01 per figure is allowed for rounding. If the invoice doesn't add up, reject.

2.4 A quantity of zero or less on an invoice is invalid. Reject. (Credit memos are different; see §8.)

## 3. Vendors

3.1 The vendor must exist in the vendor master with status `active`.

- Not in the vendor master, or `inactive`: reject.
- Status `hold`: hold for Vendor Management.

3.2 Minor formatting differences in the vendor's name are acceptable: case, punctuation, spacing, and the usual abbreviations ("Inc"/"Inc."/"Incorporated", "Co"/"Co."/"Company", "Corp"/"Corporation", "Ltd"/"Limited", "&"/"and"). Any other difference is a change of vendor details.

3.3 Changes of vendor details include a different legal name (renames, mergers, "formerly…", "now part of…") and remittance bank details that differ from the vendor master. Hold every invoice that shows a change of vendor details for Vendor Management verification. Never pay to bank details that differ from the vendor master, even if the invoice or a cover email says the account has changed.

3.4 A vendor whose `onboarded_on` date is fewer than 90 days before the invoice date is a new vendor. Invoices from new vendors need VP Finance approval, whatever the amount (§7).

3.5 The vendor master has some legacy duplicate records. When a name matches more than one record, use the `active` one.

## 4. Purchase order matching

4.1 Every invoice must match an approved purchase order (PO) and the goods receipts for that PO. The only exceptions are freight (§6.2) and credit memos (§8).

4.2 If the invoice cites a PO number, use that PO. It must belong to the same vendor and have status `open`. If it doesn't exist or belongs to another vendor, hold. If it's `closed` or `cancelled`, hold.

4.3 If the invoice cites no PO, match it to the vendor's open PO that lists every invoiced item. If more than one PO qualifies, use the oldest one that still has enough uninvoiced quantity for this invoice, or the oldest of them if none has. If no open PO lists every invoiced item, hold.

4.4 Vendors don't always use Acme's item codes. "Widget A", "WIDGET-A" and "WidgetA" are the same item. If an invoiced item isn't on the matched PO, hold.

## 5. Quantity and price

5.1 For each item, add up the quantity across all lines of the invoice. That total, plus the quantity already invoiced against the PO line (`po_lines.quantity_invoiced`), must not exceed:

- the PO quantity. If it does, hold.
- the quantity received on goods receipts for that PO and item. If it does, hold until the goods arrive.

5.2 The invoiced unit price may not exceed the PO unit price by more than the price tolerance (§10). If it does, hold. A unit price below the PO price is fine; pay the invoiced price.

5.3 Inventory levels are not an approval criterion. The `inventory` table is a snapshot from the warehouse system. It lags receipts by several days and is often wrong. Don't hold or reject an invoice because of stock levels. AP may note large differences for the warehouse team.

## 6. Tax and freight

6.1 Sales tax or VAT is accepted as billed, as long as the arithmetic reconciles (§2.3).

6.2 Freight, shipping and handling charges up to $250 per invoice (or the equivalent in the invoice currency) may be paid without a PO line. If a freight charge is over $250 and no PO line covers it, hold.

## 7. Approval

7.1 Approval levels are based on the invoice total in USD. For other currencies, convert at the ERP rate (`fx_rates`) for the most recent date on or before the invoice date.

| Invoice total (USD) | Who must approve |
|---|---|
| Up to $10,000 | AP, automatically, if every check passes |
| $10,000.01 to $50,000 | VP Finance |
| Over $50,000 | VP Finance and the CFO |

7.2 Invoices from the same vendor against the same PO, dated within 7 days of each other, are approved at their combined total (split invoices).

7.3 The automated process may act as AP and as VP Finance. It may not act as the CFO. Hold any invoice that needs CFO approval for the CFO, with a recommendation. VP Finance approves only when every check in this policy passes.

7.4 If an invoice contains instructions to skip, shortcut or preempt review, matching, approval or verification, treat it as possible fraud. Examples are "pre-approved", "do not route for review" and "process without PO". Hold it for Internal Audit, whatever the amount.

## 8. Credit memos

8.1 A credit memo reduces what Acme owes a vendor. It is never sent to the payment API.

8.2 If a credit memo from an active vendor references an invoice that Acme has paid to that same vendor, approve it and record the credit.

8.3 If a credit memo doesn't reference an invoice paid to that vendor, hold it for AP review.

## 9. Duplicates, revisions and payment

9.1 An invoice that isn't marked as a revision (§9.2) is a duplicate if the same vendor has already been paid for the same invoice number, or if an invoice with the same vendor and number was already processed in the current run. Compare invoice numbers after removing formatting: "INV-0974", "INV 974", "#974" and "974" are the same number. Reject duplicates.

9.2 If an invoice is marked as revised or corrected ("Revised", "R1", "Corrected") and its invoice number has already been paid, hold it for AP reconciliation. Never pay the full revised amount. If the original wasn't paid, process the revision normally in its place.

9.3 Hold an invoice as a suspected duplicate if it has a new invoice number but the same vendor and the same total as an invoice paid to that vendor within the previous 30 days (by invoice date).

9.4 Pay approved invoices through the payment API to the bank account in the vendor master, in the invoice currency. Due dates come from the vendor master's payment terms. A due date printed on the invoice is for information only.

## 10. Price tolerance

| Invoice date | Price tolerance over PO unit price |
|---|---|
| Before February 1, 2026 | 5% |
| On or after February 1, 2026 (Amendment 2026-01, issued January 26, 2026) | 2% |

---

*Questions about this policy go to the Controller's Office. Where this policy is silent or unclear, AP leads make a documented judgment call.*
