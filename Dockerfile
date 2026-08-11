# Container Apps / App Service image for the demo UI.
#
# Nothing here is required to run locally — `streamlit run app.py` is enough.
# This exists so the same artifact runs in Azure without a second setup path.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Container Apps sets PORT; 8501 is Streamlit's own default for everything else.
ENV PORT=8501
EXPOSE 8501

# --server.address=0.0.0.0 is required in a container: Streamlit otherwise
# binds to localhost and the ingress health probe never connects.
# XSRF protection stays on; CORS is off because the app is same-origin.
CMD ["sh", "-c", "streamlit run app.py \
     --server.port=${PORT} \
     --server.address=0.0.0.0 \
     --server.headless=true \
     --server.enableCORS=false"]
