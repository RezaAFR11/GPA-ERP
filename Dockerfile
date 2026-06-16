FROM python:3.12-slim

WORKDIR /app

# System-level deps for psycopg2 and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Uploads directory (will be overridden by volume mount in prod)
RUN mkdir -p /app/uploads

EXPOSE 8002

# Copy and set startup script (runs migrations then starts server)
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
