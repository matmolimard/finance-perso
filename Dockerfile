FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt setup.py README.md /app/
COPY portfolio_tracker /app/portfolio_tracker
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -e . && \
    python -m playwright install --with-deps chromium

EXPOSE 8765

ENTRYPOINT ["portfolio-tracker", "--data-dir", "/app/portfolio_tracker/data"]
CMD ["web", "--host", "0.0.0.0", "--port", "8765"]
