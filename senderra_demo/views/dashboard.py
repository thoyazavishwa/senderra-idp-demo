"""Tab 1 — is the pipeline working, how well, and what is it costing?

Every number here is measured, not modelled. Content Understanding reports
pages per meter and Azure OpenAI reports prompt, cached and completion tokens,
so the cost figures are the pipeline's own arithmetic read back — not an
estimate this app invented.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from senderra_demo import aggregate, charts, format as fmt, schema


def _tile(column, label: str, value: str, help_text: str) -> None:
    with column:
        st.metric(label, value, help=help_text)


def render(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("No documents found for the current filters. Upload a PDF on the "
                "**Upload** tab, or widen the run filter in the sidebar.")
        return

    k = aggregate.kpis(frame)

    st.subheader("Throughput")
    cols = st.columns(4)
    _tile(cols[0], "Total documents", fmt.num(k.documents),
          "Every document matching the current filters, at any stage.")
    _tile(cols[1], "Extracted", f"{fmt.num(k.succeeded)}  ·  {fmt.pct(k.success_rate)}",
          "Reached Succeeded, and that count as a share of SETTLED documents. "
          "The percentage excludes documents still in flight, so it does not "
          "drift while a batch is running.")
    _tile(cols[2], "In flight", fmt.num(k.in_flight),
          "Uploaded but not finished — queued for OCR, or OCR done and "
          "extraction pending.")
    _tile(cols[3], "Total pages processed", fmt.num(k.pages),
          "Pages Content Understanding billed. This is the unit the OCR meter "
          "charges on.")

    # Inputs first, then the verdict they produce — the same order as the field
    # table on the Documents tab, so "the score is the minimum of the three to
    # its left" reads the same way in both places.
    st.subheader("Quality")
    cols = st.columns(4)
    _tile(cols[0], "Avg model confidence", fmt.confidence(k.avg_model_confidence),
          "Mean across extracted documents of each document's mean field "
          "confidence. The model's own opinion of its work — the only one of the "
          "three inputs that is not a measurement. Its calibration is unmeasured, "
          "so it can lower a field score but never raise one.")
    _tile(cols[1], "Avg OCR confidence", fmt.confidence(k.avg_ocr_confidence),
          "Mean across extracted documents. Measured OCR confidence of the words "
          "each value was quoted from — independent of the model, because it "
          "measures the input.")
    _tile(cols[2], "Avg field score", fmt.confidence(k.avg_field_score),
          "Mean across extracted documents of the per-document mean. The score "
          "itself is min(model confidence, grounding, OCR) — a minimum, not an "
          "average, because an average lets a confident model wash out a failed "
          "check. Empty for documents processed before scoring existed.")
    _tile(cols[3], "Classification proven", fmt.pct(k.classification_proven),
          "Share of extracted documents where at least one of the classifier's "
          "evidence phrases was found verbatim in the text. Proves the evidence "
          "is real — NOT that the type is right.")

    st.subheader("Cost")
    cols = st.columns(4)
    _tile(cols[0], "Total spend", fmt.usd(k.total_cost_usd, 2),
          "Content Understanding plus model, summed over every document in the "
          "filtered set. Metered on both sides, not estimated.")
    _tile(cols[1], "Avg cost per document", fmt.usd(k.avg_cost_usd, 4),
          "Mean across extracted documents only. Failures are excluded — they "
          "have a partial cost but no result to attribute it to.")
    _tile(cols[2], "Avg cost per page", fmt.usd(k.cost_per_page, 4),
          "Total spend ÷ total pages — a blended rate, NOT the average of "
          "per-document rates. A 2-page document carries the same fixed prompt "
          "overhead as a 50-page one, so averaging the ratios would let short "
          "documents dominate and report a rate nobody paid.")
    _tile(cols[3], "Total prompt cache saved", fmt.usd(k.cache_saving_usd, 4),
          "Summed over the filtered set: what the same calls would have cost "
          "without the shared prompt prefix — the measured value of the "
          "two-turn design.")

    st.subheader("Tokens and speed")
    cols = st.columns(4)
    _tile(cols[0], "Avg tokens per document", fmt.num(k.avg_total_tokens),
          f"Mean across extracted documents: input plus output across both chat "
          f"calls. Those documents average {fmt.num(k.avg_pages, 1)} pages each.")
    _tile(cols[1], "Avg cached tokens", f"{fmt.num(k.avg_cached_tokens)}  ·  "
                                        f"{fmt.pct(k.avg_cache_hit)}",
          "Per document, with the mean cache hit rate beside it. Cached input is "
          "a SUBSET of prompt tokens, not an addition, and bills at 10% of list "
          "price. This is the number the whole prompt-cache design exists to move.")
    _tile(cols[2], "Avg output tokens", fmt.num(k.avg_completion_tokens),
          "Per document, both calls. Output is the most expensive token there "
          "is — roughly 8× input on this model.")
    _tile(cols[3], "Avg time per document", fmt.ms(k.avg_pipeline_ms),
          "Mean of both function invocations end to end, including queue wait. "
          "Not wall-clock through the whole system.")

    st.divider()

    st.caption("PIPELINE HEALTH")
    st.plotly_chart(
        charts.status_strip(aggregate.status_breakdown(frame), schema.STATUS_TONE),
        width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2)
    with left:
        st.caption("DOCUMENTS BY CLASSIFIED TYPE")
        st.plotly_chart(charts.by_type(aggregate.type_distribution(frame)),
                        width="stretch", config={"displayModeBar": False})
    with right:
        st.caption("FIELD SCORE DISTRIBUTION")
        st.plotly_chart(charts.score_bands(aggregate.score_bands(frame)),
                        width="stretch", config={"displayModeBar": False})
        st.caption("The spread a single average hides. The leftmost band is "
                   "below the review floor.")

    st.caption("WHERE THE TIME GOES, AVG PER DOCUMENT")
    st.plotly_chart(
        charts.stacked_by_type(
            aggregate.latency_by_type(frame),
            ["Queue wait", "OCR (Content Understanding)", "Model (2 calls)"],
            x_title="milliseconds, avg per document",
            value_fmt="%{x:,.0f} ms", empty="No timings yet"),
        width="stretch", config={"displayModeBar": False})
    st.caption("Queue wait is shared-infrastructure noise; the other two are the "
               "engine. Separated because they fail for different reasons and "
               "scale on different limits — CU on pages/minute, the model on TPM.")

    left, right = st.columns(2)
    with left:
        st.caption("WHERE THE MONEY GOES, AVG PER DOCUMENT")
        st.plotly_chart(
            charts.stacked_by_type(
                aggregate.cost_by_type(frame), ["Content Understanding", "Model"],
                x_title="USD, avg per document", value_fmt="$%{x:.4f}",
                empty="No cost recorded yet"),
            width="stretch", config={"displayModeBar": False})
        st.caption("Layout-on moves the first bar; prompt caching moves the "
                   "second. The two decisions are independent.")
    with right:
        st.caption("TOKEN MIX, AVG PER DOCUMENT")
        st.plotly_chart(
            charts.stacked_by_type(
                aggregate.token_mix_by_type(frame),
                ["Cached input", "Fresh input", "Output"],
                x_title="tokens, avg per document", value_fmt="%{x:,.0f}",
                empty="No token usage yet"),
            width="stretch", config={"displayModeBar": False})
        st.caption("Cached input bills at 10% of list. Fresh is prompt minus "
                   "cached — they are not additive.")

    # Both of these need enough data to say anything. A scatter of three points
    # and a time series of one afternoon are noise presented as insight.
    pairs = aggregate.confidence_pairs(frame)
    intake = aggregate.intake_over_time(frame, "D")

    left, right = st.columns(2)
    with left:
        st.caption("THE TWO CONFIDENCE SIGNALS")
        if len(pairs) >= 5:
            st.plotly_chart(charts.confidence_scatter(pairs), width="stretch",
                            config={"displayModeBar": False})
            st.caption("Points below the diagonal are confident answers over "
                       "poor scans — the shape most likely to survive review "
                       "while wrong.")
        else:
            st.info(f"Needs at least 5 extracted documents to be worth "
                    f"plotting — there are {len(pairs)}.")
    with right:
        st.caption("INTAKE VOLUME BY DAY")
        if len(intake) > 1:
            st.plotly_chart(charts.intake(intake, "day"), width="stretch",
                            config={"displayModeBar": False})
        else:
            st.info("Everything so far landed on one day, so there is no trend "
                    "to draw yet.")
