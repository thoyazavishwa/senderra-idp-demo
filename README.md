# Senderra IDP — demo UI

A read-only window onto the pipeline, plus an upload that starts it.

**It changes nothing about the IDP solution.** It writes to exactly one
container (`docs-in`) and reads three others. There is no API in front of the
Function App, no new queue, and no database. Delete this folder and the pipeline
is untouched.

```
streamlit run app.py
```

---

## What it shows

| Tab | Answers |
|---|---|
| **Dashboard** | Is the pipeline working, how well, and what is it costing? Throughput, the two independent confidence signals, spend split between OCR and model, latency per stage |
| **Upload** | Drop PDFs into `docs-in/<run>/`. That write is what starts the pipeline |
| **Documents** | Every document, and for one of them: extracted fields with grounding, the classifier's evidence, both stages' measurements, the OCR text, the raw records |

Every figure is **measured, not modelled**. Content Understanding reports pages
per meter and Azure OpenAI reports prompt, cached and completion tokens, so the
cost columns are the pipeline's own arithmetic read back.

---

## Setup

```bash
cd senderra-idp-demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then set STORAGE_CONNECTION_STRING
streamlit run app.py
```

If the repository root already has a configured `.env`, the app reads it and no
second copy is needed — `config.py` walks up one level.

The storage account must be the one the pipeline writes to. Read access covers
the dashboard; the upload tab additionally needs write on `docs-in`.

---

## How upload starts the pipeline

There is no API call. The pipeline is triggered by a blob write:

```
docs-in/<run_id>/<doc>.pdf  →  Event Grid  →  Service Bus  →  fn_ocr
                                                                ↓
                            work/<run>/<doc>/markdown.md  →  Event Grid  →  fn_extract
```

`parse_ocr_message` reconstructs identity from the path — first segment is the
run id, the rest is the document id. So writing to `docs-in/ui/report.pdf`
starts the real production path with zero pipeline changes. `/api/enqueue`
exists for smoke tests and is deliberately unused here.

Uploads default to run id `ui`, which keeps demo traffic separable from
benchmark runs (`r000-…`, `r002-…`) that share the same account.

---

## Why it stays fast

Streamlit re-runs the whole script on every interaction. Done naively, a
300-document corpus would re-download ~600 metrics blobs on every click —
roughly 20 seconds each time. Three things prevent that:

| | |
|---|---|
| **Diff before download** | `list_blobs` returns each blob's ETag without transferring content. These records are written once and never updated, so an unchanged ETag means an unchanged record. A steady-state refresh is **one list call and zero downloads** |
| **Parallel first load** | The unavoidable first fetch is network wait, not work. 32 threads turn ~20 s into under 1 s |
| **One store per process** | `st.cache_resource` holds the parsed records across every re-run and every session |

Two consequences worth knowing:

- **Results files are fetched lazily**, one document at a time, when you open a
  row. Loading all of them up front would triple first-load time for data only
  one document ever needs.
- **`words.json` is never read.** It is ~1 MB per document, and `blobstore.list`
  filters by suffix so it cannot enter a code path by accident. Everything the
  UI needs from it is already summarised into the extract record and the
  results file.

The sidebar prints what the last refresh actually did — listed, downloaded,
milliseconds — so the claim above is checkable rather than asserted.

---

## Layout

Dependencies point one way and never back.

```
app.py                  entry point · caching · sidebar filters · tabs
senderra_demo/
  config.py             every setting, read once
  blobstore.py          the ONLY module that imports azure.storage.blob
  store.py              joins the two stage records into one row · ETag cache
  schema.py             pipeline field names and the status vocabulary
  aggregate.py          pure: DataFrame in, chart-ready DataFrame out
  charts.py             pure: DataFrame in, Plotly figure out
  format.py             pure: value in, string out
  views/
    dashboard.py        tab 1
    upload.py           tab 2
    documents.py        tab 3
```

- No view imports the Azure SDK.
- No view computes a statistic inline — so every number on screen is testable
  from a fixture, with no storage account.
- `schema.py` names the pipeline's fields in one place, so a rename in
  `guide/11_what_gets_stored.md` breaks in exactly one file.

---

## Two things the UI says out loud, because they are true

**Classification has no proof behind its score.** The `evidence` sentence is
produced by the same model call that chose the type and is never checked
against the document. An extracted field is different — its `quote` is
string-searched in the OCR text, which is what gives a page, a bounding box and
a hallucination signal. The Classification tab states this.

**`quote_not_found` is not the same as fabrication.** On form-style documents a
value split across two labelled boxes produces a quote stitched from two distant
pieces of text, which is not a contiguous substring and so fails grounding while
being entirely correct. The Documents tab says so wherever the count is
non-zero.

---

## Deploying

Local is the expected mode. The `Dockerfile` is there so the same artifact runs
on Container Apps or App Service:

```bash
az containerapp up \
  --name senderra-idp-demo --resource-group <rg> \
  --source . --ingress external --target-port 8501 \
  --env-vars STORAGE_CONNECTION_STRING="<...>"
```

Streamlit is a single-process server holding shared state, so it does not scale
horizontally without sticky sessions — keep `min-replicas 1 --max-replicas 1`.
That is correct for a demo and is one of the reasons this is a demo tool rather
than the production UI.

---

## Scope

Read-only by design. There is no editing, no review queue, and no `needs_review`
routing — that is HITL, it needs a transactional store rather than blobs (two
reviewers editing one blob is last-write-wins with silent data loss), and it is
specified separately in `docs/senderra-idp-ui-architecture.md`.
