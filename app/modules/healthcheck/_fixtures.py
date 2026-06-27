"""Hardcoded chart of accounts + tax rates used by the audit task.

Day 6 swaps these for live pulls via Nango. Matches the EazyCapture
standard chart of accounts used across all connected Xero orgs.
"""
from __future__ import annotations

from typing import Any

HARDCODED_CHART_OF_ACCOUNTS: list[dict[str, Any]] = [
    # Balance Sheet — Liabilities
    {"code": "800", "name": "Accounts Payable",        "type": "CURRLIAB",   "vat_code": None,    "statement": "Balance Sheet"},
    {"code": "825", "name": "Employee Tax Payable",    "type": "CURRLIAB",   "vat_code": None,    "statement": "Balance Sheet"},
    {"code": "830", "name": "Income Tax Payable",      "type": "CURRLIAB",   "vat_code": None,    "statement": "Balance Sheet"},
    {"code": "840", "name": "Historical Adjustment",   "type": "EQUITY",     "vat_code": None,    "statement": "Balance Sheet"},
    # Balance Sheet — Assets
    {"code": "610", "name": "Accounts Receivable",     "type": "CURRENT",    "vat_code": None,    "statement": "Balance Sheet"},
    {"code": "720", "name": "Computer Equipment",      "type": "FIXEDASSET", "vat_code": "INPUT", "statement": "Balance Sheet"},
    # P&L — Revenue
    {"code": "270", "name": "Interest Income",         "type": "OTHERINCOME","vat_code": None,    "statement": "P&L"},
    # P&L — Direct Costs
    {"code": "310", "name": "Cost of Goods Sold",      "type": "DIRECTCOSTS","vat_code": "INPUT", "statement": "P&L"},
    # P&L — Expenses
    {"code": "400", "name": "Advertising",             "type": "EXPENSE",    "vat_code": "INPUT", "statement": "P&L"},
    {"code": "404", "name": "Bank Fees",               "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
    {"code": "408", "name": "Cleaning",                "type": "EXPENSE",    "vat_code": "INPUT", "statement": "P&L"},
    {"code": "412", "name": "Consulting & Accounting", "type": "EXPENSE",    "vat_code": "INPUT", "statement": "P&L"},
    {"code": "416", "name": "Depreciation",            "type": "DEPRECIATN", "vat_code": None,    "statement": "P&L"},
    {"code": "420", "name": "Entertainment",           "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
    {"code": "425", "name": "Freight & Courier",       "type": "EXPENSE",    "vat_code": "INPUT", "statement": "P&L"},
    {"code": "429", "name": "General Expenses",        "type": "EXPENSE",    "vat_code": "INPUT", "statement": "P&L"},
    {"code": "433", "name": "Insurance",               "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
    {"code": "437", "name": "Interest Expense",        "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
    {"code": "497", "name": "Bank Revaluations",       "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
    {"code": "505", "name": "Income Tax Expense",      "type": "EXPENSE",    "vat_code": None,    "statement": "P&L"},
]


HARDCODED_TAX_RATES: list[dict[str, Any]] = [
    {"code": "OUTPUT", "name": "20% (VAT on Income)",   "rate": "20"},
    {"code": "INPUT",  "name": "20% (VAT on Expenses)", "rate": "20"},
    {"code": "NONE",   "name": "No VAT",                 "rate": "0"},
    {"code": "TAX001", "name": "Custom Tax 001",         "rate": "20"},
]
