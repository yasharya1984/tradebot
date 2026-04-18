# ─────────────────────────────────────────────
# Stage 1: Install Python dependencies
# ─────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /app

# Install system-level build tools required by some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Core packages from requirements.txt + kiteconnect for live Zerodha trading
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "kiteconnect>=5.0.1"


# ─────────────────────────────────────────────
# Stage 2: Final slim runtime image
# ─────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from the build stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Pre-create trade_data directories so volume mounts land correctly
RUN mkdir -p trade_data/sim trade_data/live

# Make the entrypoint executable
RUN chmod +x entrypoint.sh

# ─────────────────────────────────────────────
# Runtime configuration
# ─────────────────────────────────────────────

# Tell main.py and dashboard.py they are running inside Docker
# (enables headless Streamlit, 0.0.0.0 binding, etc.)
ENV IN_DOCKER=1

# Streamlit port
EXPOSE 8501

# Default: Streamlit config (can be overridden by docker-compose env vars)
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ENABLE_CORS=false

ENTRYPOINT ["./entrypoint.sh"]
