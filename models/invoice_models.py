from typing import List, Optional
from pydantic import BaseModel, Field
from enum import Enum


class InvoiceItem(BaseModel):
    name: str = Field(..., description="Name of the item")
    quantity: int = Field(..., description="Quantity requested in the invoice")
    unit_price: Optional[float] = Field(default=None, description="Unit price of the item")


class Invoice(BaseModel):
    invoice_number: Optional[str] = Field(default=None, description="Invoice number")
    vendor: Optional[str] = Field(default=None, description="Vendor or supplier name")
    date: Optional[str] = Field(default=None, description="Invoice issued date")
    due_date: Optional[str] = Field(default=None, description="Invoice due date")
    items: List[InvoiceItem] = Field(default_factory=list, description="List of invoice items")
    total_amount: Optional[float] = Field(default=None, description="Total invoice amount")
    currency: Optional[str] = Field(default="USD", description="Invoice currency")

class IssueType(str, Enum):
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    STOCK_MISMATCH = "STOCK_MISMATCH"
    INVALID_QUANTITY = "INVALID_QUANTITY"
        
class ValidationIssue(BaseModel):
    item_name: str
    issue_type: str
    message: str


class ValidationResult(BaseModel):
    passed: bool
    issues: List[ValidationIssue] = Field(default_factory=list)
    
class ApprovalResult(BaseModel):
    approved: bool
    status: str
    reason: str
    reflection: str | None = None
    
class PaymentResult(BaseModel):
    status: str
    vendor: str | None = None
    amount: float | None = None
    reason: str | None = None