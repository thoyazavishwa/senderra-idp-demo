"""Pure functions: a documents DataFrame in, a chart-ready DataFrame out.

Nothing here touches Azure, Streamlit or Plotly, which is what makes every
number on the dashboard testable from a fixture.

ONE RULE RUNS THROUGH ALL OF IT: only documents that actually produced an
extraction contribute to quality and cost views. A failed document has no
confidence to average, and folding it in as a zero would quietly depress every
figure on the screen. Counts of failures are reported separately, as counts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from senderra_demo import schema


def _scalar(value):
    """Flatten a value for a display column, so mixed str/dict rows stay
    Arrow-serializable (pyarrow rejects an object column with mixed types)."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    """A numeric column, or an all-NaN column of the right length.

    Records written before a field existed simply lack it, so a view must never
    assume a column is present. `throttle_count` is the live example — it was
    added 2026-08-04 and older records have no such key.
    """
    if column in frame:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series([float("nan")] * len(frame), index=frame.index, dtype="float64")


def succeeded(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[frame["status"].isin(schema.SUCCESS_STATUSES)]


def _either(frame: pd.DataFrame, new: str, old: str) -> pd.Series:
    """Prefer the current column name, fall back to the pre-scoring one.

    `self_conf_mean` became `model_conf_mean` when a field grew four scores and
    "self" stopped being unambiguous. Reading both means a corpus that mixes
    backfilled and older documents produces one number instead of two half-empty
    columns.
    """
    current = _num(frame, new)
    return current.fillna(_num(frame, old)) if old in frame else current


@dataclass(frozen=True)
class Kpis:
    documents: int
    succeeded: int
    in_flight: int
    failed: int
    needs_review: int
    pages: float
    avg_model_confidence: float
    avg_ocr_confidence: float
    avg_field_score: float
    classification_proven: float
    avg_pipeline_ms: float
    total_cost_usd: float
    avg_cost_usd: float
    cost_per_page: float
    cache_saving_usd: float
    avg_cache_hit: float
    avg_grounded: float
    quote_not_found: float
    avg_pages: float
    avg_prompt_tokens: float
    avg_cached_tokens: float
    avg_completion_tokens: float
    avg_total_tokens: float

    @property
    def success_rate(self) -> float:
        settled = self.documents - self.in_flight
        return self.succeeded / settled if settled else float("nan")

    @property
    def straight_through(self) -> float:
        """Share of settled documents that tripped no review gate. Only
        meaningful once documents carry scores — pre-scoring records have no
        `review_reasons`, so they count as not-flagged and would flatter this."""
        settled = self.documents - self.in_flight
        return (settled - self.needs_review) / settled if settled else float("nan")


def kpis(frame: pd.DataFrame) -> Kpis:
    if frame.empty:
        return Kpis(0, 0, 0, 0, 0, *([float("nan")] * 17))

    done = succeeded(frame)
    in_flight = int(frame["status"].isin(schema.IN_FLIGHT_STATUSES).sum())
    review = (frame["needs_review"].eq(True).sum() if "needs_review" in frame else 0)
    proven = (done["classify_evidence_grounded"].eq(True).mean()
              if "classify_evidence_grounded" in done and not done.empty
              else float("nan"))

    return Kpis(
        documents=len(frame),
        succeeded=len(done),
        in_flight=in_flight,
        failed=int((~frame["status"].isin(schema.SUCCESS_STATUSES)
                    & ~frame["status"].isin(schema.IN_FLIGHT_STATUSES)).sum()),
        needs_review=int(review),
        pages=_num(frame, "page_count").sum(),
        avg_model_confidence=_either(done, "model_conf_mean", "self_conf_mean").mean(),
        avg_ocr_confidence=_num(done, "ocr_conf_mean").mean(),
        avg_field_score=_num(done, "field_score_mean").mean(),
        classification_proven=proven,
        avg_pipeline_ms=_num(done, "pipeline_ms").mean(),
        total_cost_usd=_num(frame, "total_cost_usd").sum(),
        avg_cost_usd=_num(done, "total_cost_usd").mean(),
        # Cost ÷ pages over the whole set, NOT the mean of per-document rates.
        # A 2-page document has the same fixed prompt overhead as a 50-page one,
        # so averaging the ratios lets short documents dominate and reports a
        # per-page cost the corpus never actually paid.
        cost_per_page=_ratio(_num(done, "total_cost_usd").sum(),
                             _num(done, "page_count").sum()),
        cache_saving_usd=_num(frame, "cache_saving_usd").sum(),
        avg_cache_hit=_num(done, "cache_hit_frac").mean(),
        avg_grounded=_num(done, "grounded_frac").mean(),
        quote_not_found=_num(done, "quote_not_found").sum(),
        avg_pages=_num(done, "page_count").mean(),
        avg_prompt_tokens=_num(done, "prompt_tokens").mean(),
        avg_cached_tokens=_num(done, "cached_tokens").mean(),
        avg_completion_tokens=_num(done, "completion_tokens").mean(),
        avg_total_tokens=(_num(done, "prompt_tokens").fillna(0)
                          + _num(done, "completion_tokens").fillna(0)).mean(),
    )


def intake_over_time(frame: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Documents per calendar period. `freq` is a pandas offset alias: D or W."""
    if frame.empty or "processed_at" not in frame:
        return pd.DataFrame(columns=["period", "documents"])

    stamped = frame.dropna(subset=["processed_at"])
    if stamped.empty:
        return pd.DataFrame(columns=["period", "documents"])

    counts = (stamped.set_index("processed_at")
              .resample(freq).size()
              .rename("documents").reset_index()
              .rename(columns={"processed_at": "period"}))
    return counts


def type_distribution(frame: pd.DataFrame) -> pd.DataFrame:
    """How many documents landed in each classified type, biggest first.

    `other` is included rather than filtered out. It is a real answer — the
    eleventh option exists so the model is not forced to pick a wrong type from
    ten that do not fit — and its rate is a headline reliability metric.
    """
    if frame.empty or "doc_type_predicted" not in frame:
        return pd.DataFrame(columns=["doc_type", "documents"])

    counts = (frame["doc_type_predicted"].dropna()
              .value_counts().rename_axis("doc_type")
              .reset_index(name="documents"))
    return counts.sort_values("documents", ascending=True)


def status_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["status", "documents"])
    counts = (frame["status"].value_counts()
              .rename_axis("status").reset_index(name="documents"))
    counts["order"] = counts["status"].map(schema.status_sort_key)
    return counts.sort_values("order").drop(columns="order")


def confidence_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    """The two independent confidence signals, one point per document.

    `self_conf_mean` is the model's opinion of its own work. `ocr_conf_mean` is
    the measured scan quality of the words it actually quoted. They are
    deliberately separate signals, so the interesting shape is where they
    DISAGREE — which is what a scatter shows and two histograms hide.
    """
    done = succeeded(frame)
    if done.empty:
        return pd.DataFrame(columns=["doc_key", "self_conf", "ocr_conf", "doc_type"])

    out = pd.DataFrame({
        "doc_key": done["doc_key"],
        "self_conf": _num(done, "self_conf_mean"),
        "ocr_conf": _num(done, "ocr_conf_mean"),
        "doc_type": done.get("doc_type_predicted", pd.Series(index=done.index, dtype=object)),
        "pages": _num(done, "page_count"),
    })
    return out.dropna(subset=["self_conf", "ocr_conf"])


def cost_over_time(frame: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """CU against model spend per period — the split the architecture argues over.

    Part-to-whole over time, so a stacked column. Kept as two components rather
    than one total because the whole design decision (layout on/off, prompt
    caching) moves one component and not the other.
    """
    if frame.empty or "processed_at" not in frame:
        return pd.DataFrame(columns=["period", "Content Understanding", "Model"])

    stamped = frame.dropna(subset=["processed_at"]).copy()
    if stamped.empty:
        return pd.DataFrame(columns=["period", "Content Understanding", "Model"])

    stamped["cu"] = _num(stamped, "cost_cu_usd").fillna(0.0)
    stamped["llm"] = _num(stamped, "cost_llm_usd").fillna(0.0)

    grouped = (stamped.set_index("processed_at")[["cu", "llm"]]
               .resample(freq).sum().reset_index()
               .rename(columns={"processed_at": "period",
                                "cu": "Content Understanding", "llm": "Model"}))
    return grouped


def latency_stages(frame: pd.DataFrame) -> pd.DataFrame:
    """Mean and p95 for each stage the pipeline times separately.

    The pipeline records four numbers rather than one because they fail for
    different reasons: queue wait is shared-infrastructure noise, CU is the OCR
    engine, and the two model calls are ours. Averaging them into a single
    end-to-end figure would hide which one moved.
    """
    done = succeeded(frame)
    stages = [
        ("Queue wait", "queue_wait_ms"),
        ("Content Understanding", "cu_latency_ms"),
        ("Classify (call 1)", "classify_ms"),
        ("Extract (call 2)", "extract_ms"),
    ]
    rows = []
    for label, column in stages:
        series = _num(done, column).dropna()
        if series.empty:
            continue
        rows.append({"stage": label,
                     "Mean": float(series.mean()),
                     "p95": float(series.quantile(0.95))})
    return pd.DataFrame(rows)


def field_rows(result: dict) -> pd.DataFrame:
    """The `fields` object of a results file, flattened for display.

    Column order is the argument the table makes: the three input signals, then
    the verdict they produce, then which one produced it. `Field score` is the
    minimum of the inputs, so a reader can check the arithmetic by eye — which is
    the whole reason for showing components instead of one opaque number.

    Reads BOTH record shapes. Documents processed before scoring existed carry a
    bare top-level `confidence` and no `scores` block, so their composite columns
    come back empty rather than wrong.
    """
    fields = (result or {}).get("fields") or {}
    rows = []
    for name, payload in fields.items():
        if not isinstance(payload, dict):
            rows.append({"Field": name, "Value": _scalar(payload)})
            continue
        grounding = payload.get("grounding") or {}
        scores = payload.get("scores") or {}
        rows.append({
            "Field": name,
            "Value": _scalar(payload.get("value")),
            "Class": payload.get("class"),
            # The three inputs to the minimum.
            "Model conf.": scores.get("model_confidence", payload.get("confidence")),
            "Grounding": scores.get("grounding_score"),
            "OCR": scores.get("ocr_score", grounding.get("ocr_min_confidence")),
            # The verdict, and which input produced it.
            "Field score": scores.get("field_score"),
            "Weakest": scores.get("weakest_signal"),
            "Review": bool(payload.get("needs_review")),
            "Why": ", ".join(payload.get("review_reasons") or []) or None,
            "Match": grounding.get("match"),
            "Page": grounding.get("page"),
            "Quote": payload.get("quote"),
        })
    return pd.DataFrame(rows)


def evidence_rows(result: dict) -> pd.DataFrame:
    """The classifier's verbatim phrases, and whether each is really on the page.

    The prose `evidence` sentence is deliberately NOT here. It paraphrases and it
    names rejected types, so it can never be located in the document — showing it
    in a table with a Found column would imply a check that cannot be run. It is
    displayed separately as what it is: the model's reasoning.
    """
    block = ((result or {}).get("classification") or {}).get("evidence_grounding") or {}
    rows = [{
        "Phrase": phrase.get("phrase"),
        "Found": bool(phrase.get("grounded")),
        "Match": phrase.get("match"),
        "Page": phrase.get("page"),
        "OCR": phrase.get("ocr_min_confidence"),
        "Score": phrase.get("score"),
    } for phrase in (block.get("phrases") or [])]
    return pd.DataFrame(rows)


def _ratio(numerator, denominator) -> float:
    return float(numerator) / float(denominator) if denominator else float("nan")


def _group(out: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    """Mean per document type, ordered so the largest bar sits at the top of a
    horizontal chart."""
    grouped = (out.dropna(subset=["doc_type"])
               .groupby("doc_type", as_index=False)[labels].mean())
    grouped["_total"] = grouped[labels].sum(axis=1)
    return grouped.sort_values("_total").drop(columns="_total")


def _mean_by_type(frame: pd.DataFrame, spec: dict[str, list[str]]) -> pd.DataFrame:
    """One row per classified type; each label is the mean of its summed sources.

    Grouped by TYPE rather than by day on purpose. A demo processes a handful of
    documents inside one afternoon, so every by-day chart collapses to a single
    bar and reads as broken. Type is the dimension that actually varies, and it
    keeps working at 540,000 documents a year.
    """
    done = succeeded(frame)
    labels = list(spec)
    if done.empty or "doc_type_predicted" not in done:
        return pd.DataFrame(columns=["doc_type", *labels])

    out = pd.DataFrame({"doc_type": done["doc_type_predicted"]}, index=done.index)
    for label, sources in spec.items():
        total = pd.Series(0.0, index=done.index)
        for name in sources:
            total = total + _num(done, name).fillna(0.0)
        out[label] = total
    return _group(out, labels)


def latency_by_type(frame: pd.DataFrame) -> pd.DataFrame:
    """Where the wall clock goes. Three buckets, not four — the two chat calls
    are one thing to a reader, and three series is comfortable in a stacked bar
    where four would demand direct labels on every segment."""
    return _mean_by_type(frame, {
        "Queue wait": ["queue_wait_ms"],
        "OCR (Content Understanding)": ["cu_latency_ms"],
        "Model (2 calls)": ["classify_ms", "extract_ms"],
    })


def cost_by_type(frame: pd.DataFrame) -> pd.DataFrame:
    """Where the money goes. The split the architecture argues over: layout-on
    moves the first bar, prompt caching moves the second."""
    return _mean_by_type(frame, {
        "Content Understanding": ["cost_cu_usd"],
        "Model": ["cost_llm_usd"],
    })


def token_mix_by_type(frame: pd.DataFrame) -> pd.DataFrame:
    """Cached against fresh input, plus output.

    ⚠️ `cached_tokens` is a SUBSET of `prompt_tokens`, not an addition. Fresh is
    therefore prompt − cached; stacking the two raw columns would double-count
    the cache hit and draw a bar twice the size of the real prompt.
    """
    done = succeeded(frame)
    labels = ["Cached input", "Fresh input", "Output"]
    if done.empty or "doc_type_predicted" not in done:
        return pd.DataFrame(columns=["doc_type", *labels])

    prompt = _num(done, "prompt_tokens").fillna(0.0)
    cached = _num(done, "cached_tokens").fillna(0.0)
    out = pd.DataFrame({
        "doc_type": done["doc_type_predicted"],
        "Cached input": cached,
        "Fresh input": (prompt - cached).clip(lower=0),
        "Output": _num(done, "completion_tokens").fillna(0.0),
    }, index=done.index)
    return _group(out, labels)


#: Ordered bands for the field-score distribution. The first boundary is
#: FIELD_SCORE_FLOOR's default, so the leftmost bar is "would be reviewed".
_SCORE_BANDS = [(0.0, 0.70, "below 0.70"), (0.70, 0.85, "0.70 – 0.85"),
                (0.85, 0.95, "0.85 – 0.95"), (0.95, 1.01, "0.95 – 1.00")]


def score_bands(frame: pd.DataFrame) -> pd.DataFrame:
    """Documents by mean field score. Shows the spread a single average hides."""
    done = succeeded(frame)
    scores = _num(done, "field_score_mean").dropna() if not done.empty else pd.Series(dtype=float)
    if scores.empty:
        return pd.DataFrame(columns=["band", "documents"])
    rows = [{"band": label, "documents": int(((scores >= lo) & (scores < hi)).sum())}
            for lo, hi, label in _SCORE_BANDS]
    return pd.DataFrame(rows)


def review_reasons(frame: pd.DataFrame) -> pd.DataFrame:
    """How many documents tripped each gate. One document can trip several."""
    if frame.empty or "review_reasons" not in frame:
        return pd.DataFrame(columns=["reason", "documents"])

    exploded = frame["review_reasons"].explode().dropna()
    if exploded.empty:
        return pd.DataFrame(columns=["reason", "documents"])
    return (exploded.value_counts().rename_axis("reason")
            .reset_index(name="documents").sort_values("documents", ascending=True))
