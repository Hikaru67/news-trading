FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/ai-ml-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY services/ai-ml-service/ .

# Create logs directory
RUN mkdir -p /app/logs

# Run the application
CMD ["python", "main.py"]
