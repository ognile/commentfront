FROM python:3.11-slim

# Install dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install playwright
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy backend code
COPY . .

# Copy frontend build (if exists)
COPY ../frontend/dist ../frontend/dist 2>/dev/null || echo 'Frontend build not found, will serve API only'

# Expose port
EXPOSE 8000

# Start command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
