FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies directly (no requirements file present)
RUN pip install --no-cache-dir \
    requests==2.31.0 \
    kafka-python==2.0.2 \
    redis==5.0.1 \
    prometheus-client==0.19.0 \
    python-dotenv==1.0.0 \
    aiohttp==3.9.1

# Copy application code
COPY services/telegram-bot/ .

# Create logs directory
RUN mkdir -p /app/logs

# Run the application
CMD ["python", "main.py"]
