# Dockerfile
FROM python:3.11-slim

# avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium

# Install chromium and fonts + required libs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      chromium \
      chromium-driver \
      wget \
      ca-certificates \
      unzip \
      fonts-liberation \
      libnss3 \
      libatk1.0-0 \
      libatk-bridge2.0-0 \
      libcups2 \
      libx11-xcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxrandr2 \
      libgbm1 \
      xdg-utils \
      libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Create app dir
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy application code
COPY . .

# Expose the port uvicorn will use
ENV PORT=8000
EXPOSE 8000

# uvicorn will serve app:app; ensure app filename matches your repo file (app.py)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "auto", "--workers", "1", "--timeout-keep-alive", "60"]
