# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --upgrade pip setuptools wheel

# Copy project files
COPY pyproject.toml README.md ./
COPY guardian/ ./guardian/

# Install all optional dependencies into /install
RUN pip install --prefix=/install ".[all]"

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY guardian/ ./guardian/
COPY config/ ./config/

# SQLite data directory
RUN mkdir -p /app/data
ENV GUARDIAN_DB_URL="sqlite:////app/data/guardian.db"

EXPOSE 8000

CMD ["uvicorn", "guardian.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
