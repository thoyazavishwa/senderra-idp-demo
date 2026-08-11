"""Streamlit views. One module per tab.

A view renders and nothing else: it receives an already-filtered DataFrame and
calls into `aggregate` and `charts` for anything derived. No view imports the
Azure SDK, and no view computes a statistic inline — that keeps every number on
screen testable without a storage account.
"""
