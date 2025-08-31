FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/websocket-rss-client/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY services/websocket-rss-client/ .

# Create logs directory
RUN mkdir -p /app/logs

# Run the application
CMD ["python", "main.py"]
