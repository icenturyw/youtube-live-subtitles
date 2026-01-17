FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install fastapi uvicorn python-multipart python-jose[cryptography] passlib[bcrypt] httpx faster-whisper opencc-python-reimplemented pymongo python-dotenv

# Copy application code
COPY . .

# Environment variables
ENV PORT=8765
ENV MODEL_SIZE=tiny
ENV DEVICE=cpu
ENV COMPUTE_TYPE=int8
ENV PYTHONPATH=/app

EXPOSE 8765

# Use the module path for uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
