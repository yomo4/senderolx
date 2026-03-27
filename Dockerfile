FROM python:3.11-slim

# System deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py olx_client.py storage.py ./

# Data directory (mounted as volume in production)
RUN mkdir -p data

CMD ["python", "-u", "bot.py"]
