FROM python:3.11-slim

# Set working directory
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /ms-playwright
RUN python -m playwright install --with-deps chromium chromium-headless-shell

# Copy backend code
COPY . .

# Start command
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
