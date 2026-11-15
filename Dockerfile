# syntax=docker/dockerfile:1
FROM python:3.14-slim

WORKDIR /app

# Prevent Python from writing pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV HOST=0.0.0.0
ENV ALLOWED_HOSTS=*
ENV DATA_DIR=/app/artifacts/workbench-data

# Copy build config, lockfile, and docs
COPY pyproject.toml README.md uv.lock ./

# Copy prebuilt workbench frontend static distribution
COPY artifacts/workbench-dist ./artifacts/workbench-dist

# Copy original support data and package source code
COPY support ./support
COPY src ./src

# Install package with web and S3 extras
RUN pip install --no-cache-dir ".[web,s3]"

# Create directory for persisted scenarios and workbench runs
RUN mkdir -p /app/artifacts/workbench-data /app/tmp

EXPOSE 8080

CMD ["gensampledata", "serve", "--host", "0.0.0.0", "--data-dir", "/app/artifacts/workbench-data"]
