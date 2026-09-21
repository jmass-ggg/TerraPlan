# FarmTwin Backend - Phase 1 Foundation
FROM python:3.12-slim

# Install system dependencies for PostGIS/geodetic libraries
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    libproj-dev \
    proj-bin \
    libgeos-dev \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files
COPY requirements.txt pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY data/ ./data/
COPY alembic.ini ./

# Create non-root user for runtime
RUN useradd -m -u 1000 farmtwin && chown -R farmtwin:farmtwin /app
USER farmtwin

# Expose API port
EXPOSE 8000

# Default command runs the API server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--timeout-graceful-shutdown", "20"]
