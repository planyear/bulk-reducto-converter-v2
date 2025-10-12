# Use a slim Python base
FROM python:3.11-slim

# Install system deps + LibreOffice for headless conversions
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
      fonts-dejavu \
      ca-certificates \
      curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app

# Copy and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Environment (Render sets PORT)
ENV PYTHONUNBUFFERED=1

# --- add these two lines ---
ENTRYPOINT ["/opt/app/entrypoint.sh"]
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","${PORT}","--proxy-headers","--forwarded-allow-ips","*"]
