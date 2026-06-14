# The Crescendo dashboard server — a tiny read-only vitrine.
# It serves the static dashboard and proxies the audit/flywheel/live views to the
# brain (mgi-mind). Stdlib http.server + python-dotenv only, so the image is small.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir python-dotenv httpx

# the dashboard server + the skill seeder (so the same image can seed the brain's
# skill libraries on first compose-up). Not the full orchestrator.
COPY dashboard/ /app/dashboard/
COPY seed_skills.py /app/seed_skills.py

# MGIMIND_URL points at the brain service on the compose network; the bearer
# token is read from the env (compose passes it from .env).
ENV MGIMIND_URL=http://brain:8765
ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "dashboard/serve.py", "--port", "8000"]
