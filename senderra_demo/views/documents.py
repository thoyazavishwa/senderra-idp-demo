"""Tab 3 — every document, and everything measured about one of them.

The table is built from data already in memory. Only the detail panel touches
the network, and only for the one document selected: its results file is a
lazy, memoised fetch. Loading all results up front would triple first-load time
to show data that one document at a time ever needs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from senderra_demo import aggregate, format as fmt, schema
from senderra_demo.store import DocumentStore

#: Table columns, in reading order: what it is, how it went, how good, what it cost.
_TABLE_COLUMNS = [
    ("doc_id", "Document"),
    ("run_id", "Run"),
    ("status", "Status"),
    ("doc_type_predicted", "Type"),
    ("classify_score", "Classify"),
    ("page_count", "Pages"),
    ("field_populated", "Fields"),
    ("field_score_mean", "Field score"),
    ("ocr_conf_mean", "OCR conf."),
    ("grounded_frac", "Grounded"),
    ("needs_review", "Review"),
    ("total_cost_usd", "Cost"),
    ("pipeline_ms", "Time"),
    ("processed_at", "Processed"),
]

_COLUMN_CONFIG = {
    "Classify": st.column_config.ProgressColumn(
        "Classify", min_value=0.0, max_value=1.0, format="%.2f",
        help="min(the classifier's own confidence, evidence grounding). Empty "
             "on documents processed before scoring existed."),
    "Field score": st.column_config.ProgressColumn(
        "Field score", min_value=0.0, max_value=1.0, format="%.2f",
        help="Mean across fields of min(model confidence, grounding, OCR)."),
    "OCR conf.": st.column_config.ProgressColumn(
        "OCR conf.", min_value=0.0, max_value=1.0, format="%.2f",
        help="Mean OCR confidence of the words each value was quoted from."),
    "Review": st.column_config.CheckboxColumn(
        "Review", help="Tripped at least one of the three gates. Recorded "
                       "only — nothing routes on it yet."),
    "Grounded": st.column_config.NumberColumn("Grounded", format="%.0f%%"),
    "Cost": st.column_config.NumberColumn("Cost", format="$%.4f"),
    "Time": st.column_config.NumberColumn("Time", format="%.1f s"),
    "Processed": st.column_config.DatetimeColumn("Processed", format="D MMM, HH:mm"),
    "Pages": st.column_config.NumberColumn("Pages", format="%d"),
    "Fields": st.column_config.NumberColumn("Fields", format="%d"),
}


def _table(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for source, label in _TABLE_COLUMNS:
        out[label] = frame[source] if source in frame else None
    # Scale for display only — the underlying frame keeps raw units.
    out["Grounded"] = pd.to_numeric(out["Grounded"], errors="coerce") * 100
    out["Time"] = pd.to_numeric(out["Time"], errors="coerce") / 1000
    # A checkbox column cannot hold NaN, and a pre-scoring document is not
    # "reviewed = false" — it has no verdict. False renders as unticked, which
    # is the closest honest thing a checkbox can say.
    out["Review"] = out["Review"].eq(True)
    return out


def render(frame: pd.DataFrame, store: DocumentStore) -> None:
    if frame.empty:
        st.info("No documents match the current filters.")
        return

    search = st.text_input("Search", placeholder="document id or type",
                           label_visibility="collapsed").strip().lower()
    if search:
        haystack = frame["doc_id"].astype(str).str.lower()
        if "doc_type_predicted" in frame:
            haystack = haystack + " " + frame["doc_type_predicted"].astype(str).str.lower()
        frame = frame[haystack.str.contains(search, na=False, regex=False)]
        if frame.empty:
            st.warning(f"Nothing matches “{search}”.")
            return

    st.caption(f"{len(frame)} document(s) · click a row for the full record")

    selection = st.dataframe(
        _table(frame), width="stretch", hide_index=True,
        column_config=_COLUMN_CONFIG, on_select="rerun",
        selection_mode="single-row", height=380, key="documents_table",
    )

    rows = selection.get("selection", {}).get("rows", []) if selection else []
    if not rows:
        st.caption("Select a row above to open the document.")
        return

    _detail(frame.iloc[rows[0]], store)


def _detail(row: pd.Series, store: DocumentStore) -> None:
    st.divider()
    st.subheader(f"{row['doc_id']}")

    tone = schema.STATUS_TONE.get(row["status"], "neutral")
    banner = {"good": st.success, "warning": st.warning,
              "serious": st.warning, "critical": st.error}.get(tone, st.info)
    detail = row.get("extract_error") or row.get("ocr_error")
    banner(f"**{row['status']}** · run `{row['run_id']}`"
           + (f" · {fmt.truncate(detail, 200)}" if isinstance(detail, str) else ""))

    reasons = row.get("review_reasons")
    if isinstance(reasons, (list, tuple)) and len(reasons):
        st.warning("**Would go to review** — "
                   + " · ".join(schema.REVIEW_REASON_HELP.get(r, r) for r in reasons))

    cols = st.columns(4)
    cols[0].metric("Type", fmt.text(row.get("doc_type_predicted")))
    cols[1].metric("Pages", fmt.num(row.get("page_count")))
    cols[2].metric("Cost", fmt.usd(row.get("total_cost_usd"), 4))
    cols[3].metric("Time", fmt.ms(row.get("pipeline_ms")))

    tabs = st.tabs(["Extracted fields", "Classification", "Stage metrics",
                    "OCR text", "Raw records"])

    with tabs[0]:
        _fields(row, store)
    with tabs[1]:
        _classification(row, store)
    with tabs[2]:
        _stage_metrics(row)
    with tabs[3]:
        _markdown(row, store)
    with tabs[4]:
        st.json(store.raw_records(row["run_id"], row["doc_id"]), expanded=False)


def _fields(row: pd.Series, store: DocumentStore) -> None:
    result = store.result(row["run_id"], row["doc_id"],
                          row.get("doc_type_predicted"), row.get("results_blob"))
    if not result:
        st.info("No results file yet. It is written when stage 2 finishes.")
        return

    rows = aggregate.field_rows(result)
    if rows.empty:
        st.info("The classifier returned `other`, so no field schema applied and "
                "no extraction was attempted. That is a designed outcome, not a "
                "failure — the eleventh type exists so the model is never forced "
                "to pick a wrong one from ten that do not fit.")
        return

    only_review = st.checkbox(
        "Only fields needing review", key=f"rev_{row['doc_key']}",
        help="What a reviewer would actually be shown. The claim that review "
             "takes 15 seconds rather than 3 minutes only holds if the queue "
             "opens on these fields and not on all of them.")
    shown = rows[rows["Review"]] if only_review and "Review" in rows else rows
    if shown.empty:
        st.success("No field tripped a check on this document.")
        return

    st.dataframe(
        shown, width="stretch", hide_index=True, height=420,
        column_config={
            # The three inputs, then the verdict. Same order as the argument.
            "Model conf.": st.column_config.ProgressColumn(
                "Model conf.", min_value=0.0, max_value=1.0, format="%.2f",
                help="The model's opinion of its own work. The only input here "
                     "that is not a measurement."),
            "Grounding": st.column_config.ProgressColumn(
                "Grounding", min_value=0.0, max_value=1.0, format="%.2f",
                help="From the match kind: exact 1.00 · whitespace 0.95 · "
                     "prefix 0.70 · value 0.60 · none 0.00."),
            "OCR": st.column_config.ProgressColumn(
                "OCR", min_value=0.0, max_value=1.0, format="%.2f",
                help="Worst word behind the quoted span. The minimum, not the "
                     "mean — one misread character makes a transcribed value "
                     "wrong and a mean hides it."),
            "Field score": st.column_config.ProgressColumn(
                "Field score", min_value=0.0, max_value=1.0, format="%.2f",
                help="The minimum of the three to its left. validation_score "
                     "is reserved and always null for now."),
            "Weakest": st.column_config.TextColumn(
                "Weakest", help="Which input produced the minimum — the reason "
                                "code a review queue would route on."),
            "Review": st.column_config.CheckboxColumn("Review"),
            "Why": st.column_config.TextColumn("Why", width="medium"),
            "Page": st.column_config.NumberColumn("Page", format="%d"),
            "Class": st.column_config.TextColumn(
                "Class", help="A transcription · B registry lookup · "
                              "C inference · D checkbox"),
            "Match": st.column_config.TextColumn(
                "Match", help="exact › whitespace › prefix › value › none. "
                              "Anything below exact is kept as its own kind "
                              "because 'paraphrased its own quote' and "
                              "'invented the value' need different responses."),
            "Quote": st.column_config.TextColumn("Quote", width="large"),
        })

    populated = fmt.safe_int(row.get("field_populated"))
    grounded = fmt.safe_int(row.get("field_grounded"))
    not_found = fmt.safe_int(row.get("quote_not_found"))
    st.caption(
        f"{populated} of {fmt.safe_int(row.get('field_count'))} fields populated · "
        f"{grounded} grounded · {not_found} quote(s) not found in the document."
        + (" A non-zero count on form-style documents is usually a quote stitched "
           "from two distant labelled boxes, not a fabricated value — read the "
           "quotes before concluding anything." if not_found else ""))


def _classification(row: pd.Series, store: DocumentStore) -> None:
    cols = st.columns(4)
    cols[0].metric("Predicted type", fmt.text(row.get("doc_type_predicted")))
    cols[1].metric("Model confidence", fmt.confidence(row.get("classify_confidence")))
    cols[2].metric("Evidence score", fmt.confidence(row.get("classify_evidence_score")))
    cols[3].metric("Classification score", fmt.confidence(row.get("classify_score")))

    evidence = row.get("classify_evidence")
    if not (isinstance(evidence, str) and evidence):
        # Absent when the table was built from the Cosmos projection, which
        # strips this field because it is up to 500 characters of document prose
        # — PHI, and it must not reach a store that feeds a dashboard. The blob
        # record still has it, and this screen is already reading one document.
        evidence = (store.raw_records(row["run_id"], row["doc_id"])
                    .get("extract", {}).get("classify_evidence"))

    st.write("**The model's reasoning**")
    st.info(evidence if isinstance(evidence, str) and evidence else
            "No evidence recorded.")
    st.caption("Prose — it paraphrases and it names rejected types, so it can "
               "never be located in the document. It is here to be read, not "
               "checked.")

    st.write("**The deterministic proof**")
    result = store.result(row["run_id"], row["doc_id"],
                          row.get("doc_type_predicted"), row.get("results_blob"))
    phrases = aggregate.evidence_rows(result or {})

    if phrases.empty:
        st.warning(
            "No evidence phrases on this document. It was processed before the "
            "classifier was asked for verbatim proof, so its type rests on the "
            "model's word alone. Re-running stage 2 via `POST /api/reextract` "
            "backfills it without re-doing OCR.")
        return

    st.dataframe(
        phrases, width="stretch", hide_index=True,
        column_config={
            "Phrase": st.column_config.TextColumn("Phrase", width="large"),
            "Found": st.column_config.CheckboxColumn(
                "Found", help="Is this passage really in the document text?"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0.0, max_value=1.0, format="%.2f"),
            "OCR": st.column_config.NumberColumn("OCR", format="%.3f"),
            "Page": st.column_config.NumberColumn("Page", format="%d"),
        })

    found = int(phrases["Found"].sum())
    st.caption(
        f"{found} of {len(phrases)} phrases found verbatim in the document. "
        "The score is the **best** match, not the average: one passage really "
        "on the page is proof, and phrases that missed do not unprove it — "
        "how many missed is reported here as a separate quality signal.")
    st.warning(
        "This proves the evidence is **real**. It does not prove the type is "
        "**right** — a denial letter with a prescription attached contains "
        "genuine prescription wording, so a perfectly grounded phrase can still "
        "sit behind a wrong answer. Catching that needs the schema-fit "
        "back-check, which is not implemented.")


def _stage_metrics(row: pd.Series) -> None:
    def _block(title: str, pairs: list[tuple[str, str]]) -> None:
        st.write(f"**{title}**")
        st.dataframe(pd.DataFrame(pairs, columns=["Metric", "Value"]),
                     width="stretch", hide_index=True)

    _block("Stage 1 — OCR", [
        ("Analyzer", fmt.text(row.get("analyzer_id"))),
        ("Queue wait", fmt.ms(row.get("queue_wait_ms"))),
        ("Content Understanding", fmt.ms(row.get("cu_latency_ms"))),
        ("Pages billed", f"{fmt.num(row.get('pages_standard'))} standard · "
                         f"{fmt.num(row.get('pages_basic'))} basic"),
        ("Words found", fmt.num(row.get("word_count"))),
        ("Markdown size", f"{fmt.num(row.get('markdown_chars'))} chars"),
        ("Mean page confidence", fmt.confidence(row.get("mean_page_confidence"))),
        ("Worst page confidence", fmt.confidence(row.get("min_page_confidence"))),
        ("CU cost", fmt.usd(row.get("cost_cu_usd"), 4)),
    ])

    _block("Stage 2 — classify and extract", [
        ("Model", fmt.text(row.get("model_deployment"))),
        ("Prompt version", fmt.text(row.get("prompt_sha"))),
        ("Classify (call 1)", fmt.ms(row.get("classify_ms"))),
        ("Extract (call 2)", fmt.ms(row.get("extract_ms"))),
        ("Prompt tokens", fmt.num(row.get("prompt_tokens"))),
        ("of which cached", f"{fmt.num(row.get('cached_tokens'))}  "
                            f"({fmt.pct(row.get('cache_hit_frac'))})"),
        ("Completion tokens", fmt.num(row.get("completion_tokens"))),
        ("Reasoning tokens", fmt.num(row.get("reasoning_tokens"))),
        ("Model cost", fmt.usd(row.get("cost_llm_usd"), 4)),
        ("Cache saved", fmt.usd(row.get("cache_saving_usd"), 4)),
        ("Total", fmt.usd(row.get("total_cost_usd"), 4)),
    ])
    st.caption("Cached tokens are a **subset** of prompt tokens, not an "
               "addition — billing is (prompt − cached) at full rate plus "
               "cached at 10%.")


def _markdown(row: pd.Series, store: DocumentStore) -> None:
    st.caption("The OCR checkpoint. Neither model call ever sees the PDF — this "
               "text is the entire input to both.")
    if not st.button("Load OCR text", key=f"md_{row['doc_key']}"):
        return
    text = store.markdown(row["run_id"], row["doc_id"])
    if text is None:
        st.info("No markdown checkpoint yet — stage 1 has not finished.")
        return
    st.text_area("markdown.md", value=text, height=420, label_visibility="collapsed")
