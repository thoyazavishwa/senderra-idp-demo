"""Plotly figures, built to one spec.

COLOUR IS NOT A TASTE DECISION HERE
-----------------------------------
The categorical slots below were validated against the light chart surface
(#fcfcfb) for colour-vision-deficiency separation and normal-vision distance,
under the all-pairs rule (the strictest — it covers the scatter):

    worst all-pairs CVD ΔE 9.2 (deutan)      target ≥ 8
    worst all-pairs normal-vision ΔE 24.0    floor  ≥ 15

Slot 3 (aqua) measures 2.74:1 against the surface, below the 3:1 bar, so it is
NOT used for any fill that has to be told apart by colour alone — every chart
here needs at most two categorical series, which slots 1 and 2 both clear. If a
third series is ever genuinely needed, it must arrive with direct labels.

Three rules follow from the same source and are applied throughout:

* **One axis, ever.** No figure has two y-scales. Where two measures of
  different scale would be interesting together, they are two figures.
* **Magnitude uses one hue, light→dark; identity uses the categorical slots.**
  So "documents per type" is a blue ramp (it is a comparison of sizes), while
  "CU cost vs model cost" is blue + orange (they are different things).
* **Colour never carries meaning alone.** Two or more series always ship a
  legend; status bars always ship their label.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# --- validated palette ------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

SERIES_1 = "#2a78d6"   # blue
SERIES_2 = "#eb6834"   # orange
#: Slot 3 is VIOLET here, not the palette's default aqua. Aqua measures 2.74:1
#: against this surface, and a sub-3:1 fill obliges direct labels on every
#: segment — unreadable on a thin stacked bar. Violet was validated as a
#: replacement and is strictly better on all three gates:
#:     all-pairs CVD ΔE 13.0 (vs 9.2)   normal-vision ΔE 16.3   contrast all >= 3:1
#: Green was the other candidate and FAILS: ΔE 3.2 under protanopia against
#: orange. Re-run scripts/validate_palette.js before substituting anything here.
SERIES_3 = "#4a3aa7"   # violet

#: Blue, light→dark. Starts at step 250 rather than 100: on the light surface
#: anything lighter drops below 2:1 and the smallest bar would vanish into the
#: background.
SEQUENTIAL = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
              "#2a78d6", "#256abf", "#184f95", "#0d366b"]

#: Reserved. Never used as a series colour, always paired with its label.
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
    "neutral": "#898781",
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

#: A 1px stroke in the surface colour on every fill. On adjacent and stacked
#: bars it reads as the 2px gap that keeps segments from fusing into one block.
_SPACER = dict(line=dict(color=SURFACE, width=1))


def _layout(fig: go.Figure, height: int = 300, legend: bool = False,
            y_title: str | None = None, x_title: str | None = None) -> go.Figure:
    """Chrome shared by every figure: recessive grid, no chartjunk, one axis."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK_SECONDARY),
        hoverlabel=dict(font=dict(family=FONT, size=12), bgcolor=SURFACE,
                        bordercolor=BASELINE, font_color=INK),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(color=INK_SECONDARY)),
        bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=BASELINE,
                     ticks="outside", tickcolor=BASELINE, ticklen=4,
                     tickfont=dict(color=INK_MUTED), title=x_title,
                     title_font=dict(color=INK_MUTED, size=11))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", tickfont=dict(color=INK_MUTED),
                     title=y_title, title_font=dict(color=INK_MUTED, size=11))
    return fig


def _empty(message: str = "No data yet") -> go.Figure:
    """A figure that says so, rather than an axis frame around nothing."""
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False,
                       font=dict(family=FONT, size=13, color=INK_MUTED))
    fig.update_layout(height=260, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      xaxis=dict(visible=False), yaxis=dict(visible=False),
                      margin=dict(l=8, r=8, t=8, b=8))
    return fig


def _ramp(values: pd.Series) -> list[str]:
    """Map magnitudes onto the sequential ramp — bigger is darker."""
    if values.empty:
        return []
    low, high = float(values.min()), float(values.max())
    span = high - low
    last = len(SEQUENTIAL) - 1
    if span == 0:
        return [SEQUENTIAL[last]] * len(values)
    return [SEQUENTIAL[round((float(v) - low) / span * last)] for v in values]


# --- the figures ------------------------------------------------------------
def intake(frame: pd.DataFrame, period_label: str = "day") -> go.Figure:
    """Documents over time. One series, so one hue and no legend — the title
    already names what is being counted."""
    if frame.empty:
        return _empty("Nothing processed yet")

    fig = go.Figure(go.Bar(
        x=frame["period"], y=frame["documents"],
        marker=dict(color=SERIES_1, cornerradius=4, **_SPACER),
        hovertemplate="%{x|%d %b %Y}<br><b>%{y}</b> documents<extra></extra>",
    ))
    return _layout(fig, height=280, y_title=f"documents per {period_label}")


def by_type(frame: pd.DataFrame) -> go.Figure:
    """Comparison of magnitudes across eleven categories — a sequential ramp,
    not eleven hues. Horizontal because the type names are long."""
    if frame.empty:
        return _empty("No classified documents yet")

    fig = go.Figure(go.Bar(
        x=frame["documents"], y=frame["doc_type"], orientation="h",
        marker=dict(color=_ramp(frame["documents"]), cornerradius=4, **_SPACER),
        text=frame["documents"], textposition="outside",
        textfont=dict(color=INK_SECONDARY, size=11),
        hovertemplate="<b>%{y}</b><br>%{x} documents<extra></extra>",
    ))
    fig = _layout(fig, height=max(280, 26 * len(frame) + 60), x_title="documents")
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


def stacked_by_type(frame: pd.DataFrame, labels: list[str], *,
                    x_title: str, value_fmt: str = "%{x:,.0f}",
                    empty: str = "Nothing to show yet") -> go.Figure:
    """Composition per document type, as a horizontal stacked bar.

    Horizontal because type names are long, stacked because the components sum
    to something a reader cares about (total time, total cost, total tokens),
    and by type because that dimension varies at every corpus size — a by-day
    chart of one afternoon's demo is a single bar.
    """
    if frame.empty:
        return _empty(empty)

    fig = go.Figure()
    for name, colour in zip(labels, (SERIES_1, SERIES_2, SERIES_3)):
        fig.add_trace(go.Bar(
            name=name, x=frame[name], y=frame["doc_type"], orientation="h",
            marker=dict(color=colour, cornerradius=4, **_SPACER),
            hovertemplate=f"{name} · %{{y}}<br><b>{value_fmt}</b><extra></extra>",
        ))
    fig.update_layout(barmode="stack")
    fig = _layout(fig, height=max(240, 32 * len(frame) + 90), legend=True,
                  x_title=x_title)
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


def score_bands(frame: pd.DataFrame) -> go.Figure:
    """Documents by field-score band — an ordered scale, so one hue light→dark
    rather than four identities."""
    if frame.empty:
        return _empty("No scored documents yet")

    fig = go.Figure(go.Bar(
        x=frame["band"], y=frame["documents"],
        marker=dict(color=SEQUENTIAL[1::2][:len(frame)], cornerradius=4, **_SPACER),
        text=frame["documents"], textposition="outside",
        textfont=dict(color=INK_SECONDARY, size=11),
        hovertemplate="<b>%{y}</b> documents<br>field score %{x}<extra></extra>",
    ))
    return _layout(fig, height=260, y_title="documents")


def review_gates(frame: pd.DataFrame, labels: dict[str, str]) -> go.Figure:
    """Which gate sent documents to review. Magnitudes, so one hue, not three.

    Deliberately NOT the status palette: these are counts of a routing outcome,
    not a health state, and reusing a status colour here would let "needs
    classification review" read as an error.
    """
    if frame.empty:
        return _empty("No document has tripped a review gate")

    pretty = [labels.get(r, r) for r in frame["reason"]]
    fig = go.Figure(go.Bar(
        x=frame["documents"], y=pretty, orientation="h",
        marker=dict(color=_ramp(frame["documents"]), cornerradius=4, **_SPACER),
        text=frame["documents"], textposition="outside",
        textfont=dict(color=INK_SECONDARY, size=11),
        hovertemplate="<b>%{y}</b><br>%{x} documents<extra></extra>",
    ))
    fig = _layout(fig, height=max(200, 34 * len(frame) + 70), x_title="documents")
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


def confidence_scatter(frame: pd.DataFrame) -> go.Figure:
    """Self-reported confidence against measured OCR confidence.

    The diagonal is the reference: points on it mean the two independent signals
    agree. The interesting documents are the ones far off it — high self-
    confidence over poor scan quality (below the line) is the shape that
    survives review and shouldn't.
    """
    if frame.empty:
        return _empty("No extracted documents yet")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0.4, 1.0], y=[0.4, 1.0], mode="lines", showlegend=False,
        line=dict(color=BASELINE, width=1, dash="dot"), hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=frame["ocr_conf"], y=frame["self_conf"], mode="markers",
        showlegend=False,
        marker=dict(size=9, color=SERIES_1, opacity=0.75,
                    line=dict(color=SURFACE, width=2)),
        customdata=frame[["doc_key", "doc_type"]],
        hovertemplate=("<b>%{customdata[0]}</b><br>%{customdata[1]}"
                       "<br>OCR %{x:.3f} · self %{y:.3f}<extra></extra>"),
    ))
    fig = _layout(fig, height=300,
                  x_title="OCR confidence (measured)",
                  y_title="self-reported confidence")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, range=[0.4, 1.02])
    fig.update_yaxes(range=[0.4, 1.02])
    return fig


def cost_split(frame: pd.DataFrame) -> go.Figure:
    """Part-to-whole over time: Content Understanding against model spend.

    Two genuinely different things, so two categorical hues rather than a ramp,
    and a legend because there are two series.
    """
    if frame.empty:
        return _empty("No cost recorded yet")

    fig = go.Figure()
    for name, colour in (("Content Understanding", SERIES_1), ("Model", SERIES_2)):
        fig.add_trace(go.Bar(
            name=name, x=frame["period"], y=frame[name],
            marker=dict(color=colour, cornerradius=4, **_SPACER),
            hovertemplate=f"{name}<br>%{{x|%d %b}}<br><b>$%{{y:.4f}}</b><extra></extra>",
        ))
    fig.update_layout(barmode="stack")
    return _layout(fig, height=280, legend=True, y_title="USD")


def latency(frame: pd.DataFrame) -> go.Figure:
    """Mean and p95 per stage, on one shared millisecond axis.

    Stages are separate bars rather than a stacked total because they fail for
    different reasons — queue wait is shared-infrastructure noise, the rest is
    the engine — and a single end-to-end number hides which one moved.
    """
    if frame.empty:
        return _empty("No timings yet")

    fig = go.Figure()
    for name, colour in (("Mean", SERIES_1), ("p95", SERIES_2)):
        fig.add_trace(go.Bar(
            name=name, x=frame[name], y=frame["stage"], orientation="h",
            marker=dict(color=colour, cornerradius=4, **_SPACER),
            hovertemplate=f"{name} · %{{y}}<br><b>%{{x:,.0f}} ms</b><extra></extra>",
        ))
    fig.update_layout(barmode="group")
    fig = _layout(fig, height=300, legend=True, x_title="milliseconds")
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


def status_strip(frame: pd.DataFrame, tone_map: dict[str, str]) -> go.Figure:
    """Pipeline health as one part-to-whole bar.

    Uses the reserved status palette, never the categorical slots, so a status
    can never be mistaken for a series — and every segment is labelled, because
    two of the four status steps sit below 3:1 on a light surface and must not
    rely on colour.
    """
    if frame.empty:
        return _empty("Nothing to report")

    fig = go.Figure()
    for _, row in frame.iterrows():
        tone = tone_map.get(row["status"], "neutral")
        fig.add_trace(go.Bar(
            name=row["status"], x=[row["documents"]], y=[""], orientation="h",
            marker=dict(color=STATUS[tone], cornerradius=4, **_SPACER),
            text=f"{row['status']} {row['documents']}", textposition="auto",
            # Dark ink, not white. Two of the four status steps (warning
            # #fab219, serious #ec835a) are light enough that white text on them
            # falls under 2:1 — and these labels are the thing that stops colour
            # from carrying the meaning alone, so they cannot be the part that
            # is hard to read. #0b0b0b clears 4.5:1 on all five fills.
            insidetextfont=dict(color=INK, size=11),
            outsidetextfont=dict(color=INK_SECONDARY, size=11),
            hovertemplate=f"{row['status']}<br><b>%{{x}}</b> documents<extra></extra>",
        ))
    fig.update_layout(barmode="stack")
    fig = _layout(fig, height=96)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, showgrid=False)
    return fig
