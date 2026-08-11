"""The contract between the pipeline's JSON records and this app's table.

Field names come from `guide/11_what_gets_stored.md`, which documents all 45
stage-1 and 57 stage-2 fields. Naming them here rather than reaching into dicts
at the point of use means a pipeline field rename breaks in exactly one file.

TWO STAGE RECORDS, ONE ROW
--------------------------
`fn_ocr` and `fn_extract` write separate blobs on purpose — they race otherwise,
and the OCR record must survive when extraction fails. This app joins them back
into one row per document, which means resolving the keys that appear in BOTH
records with different meanings. `status`, `e2e_latency_ms` and `recorded_at`
are stage-scoped and are therefore renamed on the way in; everything else is
either stage-unique or genuinely the same value carried forward.
"""
from __future__ import annotations

# --- how a metrics blob name decomposes -----------------------------------
OCR_SUFFIX = ".ocr.json"
EXTRACT_SUFFIX = ".extract.json"
METRIC_SUFFIXES = (OCR_SUFFIX, EXTRACT_SUFFIX)

# --- keys that collide across the two stages and must be disambiguated -----
OCR_RENAMES = {
    "status": "ocr_status",
    "e2e_latency_ms": "ocr_e2e_ms",
    "recorded_at": "ocr_recorded_at",
    "error_message": "ocr_error",
    "attempt": "ocr_attempt",
}
EXTRACT_RENAMES = {
    "status": "extract_status",
    "e2e_latency_ms": "extract_e2e_ms",
    "recorded_at": "extract_recorded_at",
    "error_message": "extract_error",
    "attempt": "extract_attempt",
}

# Nested objects are dropped from the flat table and read from the raw record
# on the detail screen. `calls` is a 2-element list, `pages` is one entry per
# page, `cost_warnings` is a list of strings — none of them belong in a column.
DROP_FROM_TABLE = ("calls", "pages", "cost_warnings", "config")

# --- columns coerced to numbers --------------------------------------------
# Records written before a field existed simply lack it, and a column that is
# sometimes absent must still be numeric where present. Anything not in this
# list stays whatever JSON made it.
NUMERIC_COLUMNS = (
    "page_count", "word_count", "markdown_chars", "file_bytes",
    "table_count", "section_count", "barcode_count",
    "mean_page_confidence", "min_page_confidence",
    "queue_wait_ms", "cu_submit_ms", "cu_poll_ms", "cu_latency_ms",
    "ocr_e2e_ms", "extract_e2e_ms", "classify_ms", "extract_ms",
    "ocr_latency_ms", "poll_count", "cu_throttle_count", "throttle_count",
    "pages_minimal", "pages_basic", "pages_standard", "pages_billed",
    "contextualization_tokens", "cu_model_tokens",
    "cost_cu_extraction_usd", "cost_cu_contextualization_usd", "cost_cu_usd",
    "prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens",
    "cache_hit_frac", "cost_llm_usd", "cost_llm_uncached_usd",
    "cache_saving_usd", "total_cost_usd", "cost_per_1k_pages_usd",
    "field_count", "field_populated", "field_null",
    "field_grounded", "field_grounded_exact", "grounded_frac",
    "quote_not_found", "quote_not_found_frac",
    "ocr_conf_mean", "ocr_conf_min",
    "fields_below_confidence_floor",

    # Classification, deterministic half. The model's own number is
    # `classify_confidence`; everything below is measured against the document.
    "classify_confidence", "classify_score",
    "classify_evidence_phrases", "classify_phrases_grounded",
    "classify_evidence_score", "classify_evidence_grounded_frac",

    # Field scoring. `model_conf_*` is the model's opinion; `field_score_*` is
    # the composite minimum that a review queue would actually route on.
    "model_conf_mean", "model_conf_min",
    "field_score_mean", "field_score_min", "fields_needing_review",

    # ⚠️ Pre-scoring records call these `self_conf_*`. Both names are coerced so
    # a corpus that mixes old and backfilled documents renders in one pass;
    # `aggregate` reads the new name and falls back to the old.
    "self_conf_mean", "self_conf_min",
)

#: Document-level review gates. Stored by the pipeline, acted on by nobody yet.
REVIEW_OCR = "ocr_needs_review"
REVIEW_CLASSIFICATION = "classification_needs_review"
REVIEW_EXTRACTION = "extraction_needs_review"

#: What each gate means, for tooltips. The three are separate because they route
#: to three different actions, not because they are three flavours of "bad".
REVIEW_REASON_HELP = {
    REVIEW_OCR: "The scan is illegible or the text was truncated — rescan, "
                "do not re-extract.",
    REVIEW_CLASSIFICATION: "The type is unproven: no evidence phrase was found "
                           "in the document, the score is below the floor, or "
                           "the classifier returned `other`.",
    REVIEW_EXTRACTION: "At least one field failed a check — ungrounded value, "
                       "quote not in the document, or low OCR confidence.",
}

#: Per-field reason codes, worst first.
FIELD_REASON_HELP = {
    "quote_not_found": "The quote the model gave is nowhere in the document.",
    "ungrounded": "Neither the quote nor the value could be located.",
    "malformed_field": "The model broke its own output schema for this field.",
    "weak_grounding": "Located only by prefix, or by the value after the quote "
                      "failed — the model paraphrased its own quote.",
    "low_ocr_confidence": "The words behind this value scanned poorly.",
    "low_model_confidence": "The model said it was unsure.",
    "low_field_score": "No single check failed outright, but the weakest "
                       "signal is below the floor.",
}

TIMESTAMP_COLUMNS = ("ocr_recorded_at", "extract_recorded_at", "enqueued_at")

# --- status ----------------------------------------------------------------
# From guide/11, plus two this app synthesises because they describe a document
# that has no record yet rather than a record with a value.
STATUS_QUEUED = "Queued"        # PDF in docs-in, stage 1 has not written
STATUS_PROCESSING = "Processing"    # stage 1 done, stage 2 has not written
STATUS_SUCCEEDED = "Succeeded"
STATUS_OTHER = "ClassifiedOther"
STATUS_FILTERED = "ContentFiltered"
STATUS_FAILED = "Failed"

#: Statuses that mean the document produced a usable extraction. Only these
#: contribute to accuracy and confidence views — a failed document has no
#: confidence to average, and including it as a zero would be a lie.
SUCCESS_STATUSES = frozenset({STATUS_SUCCEEDED})

#: Still moving. Excluded from throughput and cost, counted separately.
IN_FLIGHT_STATUSES = frozenset({STATUS_QUEUED, STATUS_PROCESSING})

#: How statuses sort in the UI: worst first, because that is what a reader is
#: looking for.
STATUS_ORDER = (
    STATUS_FAILED, "SubmitFailed", "PollTimeout", STATUS_FILTERED,
    STATUS_OTHER, STATUS_PROCESSING, STATUS_QUEUED,
    STATUS_SUCCEEDED, "DuplicateSkipped",
)

#: Status → the four reserved status-palette roles. Never a categorical hue,
#: and always shown with its label so colour is not carrying the meaning.
STATUS_TONE = {
    STATUS_SUCCEEDED: "good",
    "DuplicateSkipped": "good",
    STATUS_QUEUED: "neutral",
    STATUS_PROCESSING: "neutral",
    STATUS_OTHER: "warning",
    STATUS_FILTERED: "serious",
    STATUS_FAILED: "critical",
    "SubmitFailed": "critical",
    "PollTimeout": "critical",
}


def status_sort_key(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return len(STATUS_ORDER)
