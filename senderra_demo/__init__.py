"""Senderra IDP demo UI.

A read-only window onto the pipeline's blob output, plus an upload that drops a
PDF where Event Grid is already watching. It writes to exactly one container
(`docs-in`) and changes nothing about the IDP solution.

Layering — dependencies point one way and never back:

    views/      Streamlit. Renders. No Azure SDK, no aggregation logic.
      |
    store       Joins two stage records into one row. Caches by ETag.
      |
    blobstore   The only module that imports azure.storage.blob.

    aggregate   Pure: DataFrame in, chart-ready DataFrame out. No I/O.
    charts      Pure: DataFrame in, Plotly figure out. Validated palette.
    format      Pure: value in, string out.
    schema      Field names and status vocabulary. No behaviour.
"""

__version__ = "1.0.0"
