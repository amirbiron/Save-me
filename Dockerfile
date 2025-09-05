FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy application source
COPY src/ .
# Copy root-level modules required by imports
COPY config.py activity_reporter.py ./
CMD ["python", "main.py"]
