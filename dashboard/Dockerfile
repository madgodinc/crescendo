# The Crescendo dashboard server — a tiny read-only vitrine.
# It serves the static dashboard and proxies the audit/flywheel/live views to the
# brain (mgi-mind). Stdlib http.server + python-dotenv only, so the image is small.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir python-dotenv

# only the dashboard assets + server are needed here (not the orchestrator)
COPY dashboard/ /app/dashboard/

# MGIMIND_URL points at the brain service on the compose network; the bearer
# token is read from the env (compose passes it from .env).
ENV MGIMIND_URL=http://brain:8765
ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "dashboard/serve.py", "--port", "8000"]
