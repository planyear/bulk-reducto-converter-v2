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

# Work inside /app
WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code and start script
COPY . .
RUN chmod +x /app/start.sh

# Unbuffered logs
ENV PYTHONUNBUFFERED=1

# Start the app (shell not required; script is executable)
CMD ["/app/start.sh"]
