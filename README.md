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

Settings are read from three places, in precedence order: real environment
variables, then Streamlit secrets (`st.secrets` — how the deployed app is
configured), then `.env`. Same keys throughout, so a setting behaves identically
wherever it came from. `APP_PASSWORD` is optional locally and required on a
public host — see [Deploying to Streamlit Community
Cloud](#deploying-to-streamlit-community-cloud).

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
| **Parallel first load** | The unavoidable first fetch is network wait, not work. 16 threads turn ~20 s into a couple |
| **One store per process** | `st.cache_resource` holds the parsed records across every re-run and every session — so the corpus is downloaded once for the whole app, not once per viewer |
| **One refresh at a time** | Concurrent sessions do not each re-list. The first to arrive does the work; the rest are handed the snapshot and told so |

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
  auth.py               the shared-password gate, in front of everything
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

## Deploying to Streamlit Community Cloud

Five steps. The app needs no code change to run there — Cloud injects settings
through `st.secrets`, which `config.py` folds into the same `os.environ` the
`.env` path uses.

**1. Push this folder to GitHub.** Cloud deploys from a repo, not from a
directory. Confirm `.env` is not in it: `git ls-files | grep env` should show
only `.env.example`.

**2. Mint a scoped SAS** — free to create, and the credential a public URL should
be carrying. It expires, and it reaches four containers instead of the whole
account:

```bash
az storage account generate-sas \
  --account-name <account> --account-key <key> \
  --services b --resource-types co \
  --permissions rlacw \
  --expiry $(date -u -d '+30 days' '+%Y-%m-%dT%H:%MZ') \
  --https-only --output tsv
```

`rl` reads and lists (dashboard), `acw` adds/creates/writes (upload tab). Nothing
grants delete, so a leaked URL cannot destroy pipeline data. Assemble the result
into a connection string:

```
BlobEndpoint=https://<account>.blob.core.windows.net/;SharedAccessSignature=<the sas above>
```

The account-key string works too and needs no extra step — it simply grants far
more than this app uses, which is fine on your desk and not fine on a public URL.
The sidebar prints which of the two is in play.

**3. Deploy.** [share.streamlit.io](https://share.streamlit.io) → *Create app* →
pick the repo, branch `main`, main file `app.py`. Under *Advanced settings* choose
**Python 3.12** and paste the secrets, using
[`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) as the
template:

```toml
STORAGE_CONNECTION_STRING = "BlobEndpoint=https://…;SharedAccessSignature=sv=…"
APP_PASSWORD = "<something you are willing to say out loud on a call>"
```

**4. `APP_PASSWORD` is not optional here.** A Community Cloud URL is reachable by
anyone who has it, and this app's Upload tab starts billed pipeline runs — so on
a cloud host the app refuses to start without one, rather than defaulting open.
It is one shared password, not authentication: no accounts, no audit trail. If
the demo needs to know *who* looked, use Cloud's viewer allowlist (*Settings →
Sharing*) on top, or put an identity provider in front.

**5. Editing secrets restarts the app.** Settings are read once per process into
`st.cache_resource`, so a changed secret takes effect on the reboot Cloud performs
when you save — not before.

### What three to five people at once actually costs

`st.cache_resource` means one `DocumentStore` serves every viewer, so the corpus
is downloaded once per process and not once per person. What each additional
viewer adds is a browser session and some re-runs, not a copy of the data.

| | |
|---|---|
| **First screen after unlock** | A few seconds — every metrics record, downloaded in parallel. Once, for the whole app, not per viewer. A spinner says so |
| **Every interaction after that** | One list call per `SYNC_TTL_SECONDS`, zero downloads while ETags match. Filters and tab switches touch no network at all |
| **Two people refreshing together** | The first does the work; the second is handed the existing snapshot and the sidebar says *another session was refreshing*. No viewer ever waits on another's round trip |
| **Opening a document** | One blob per document, memoised, capped at 256 parsed results |

Three limits are worth knowing before the demo rather than during it. Community
Cloud gives the app roughly **1 GB of RAM for everything and everyone** — hence
`MAX_WORKERS=16` (multiplied by viewers, not shared between them), the 256-entry
results cap, and `UPLOAD_BATCH_MB=100`, since Streamlit buffers uploads in the
server process and the SDK copies them again. Cloud also **sleeps an idle app**,
so the first visitor after a quiet spell pays a cold start of a minute or so —
open it yourself before a client call. And Streamlit is **one process holding
shared state**: it does not scale horizontally, which is fine for five viewers
and is one reason this is a demo tool rather than the production UI.

Azure failures never take the app down. A refresh that fails keeps serving the
last good snapshot and reports why in the sidebar, redacted — an Azure error
quotes the request URL, and under a SAS credential that URL *is* the credential,
which is also why `showErrorDetails = "none"` sends tracebacks to the Cloud log
instead of the browser.

---

## Deploying anywhere else

The `Dockerfile` is there so the same artifact runs on Container Apps or App
Service:

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
