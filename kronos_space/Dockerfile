FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Create non-root user (HF Spaces convention)
RUN useradd -m -u 1000 user
WORKDIR /app

# Clone Kronos source (no PyPI package exists)
RUN git clone --depth 1 https://github.com/shiyu-coder/Kronos.git /app/Kronos

# Install CPU-only PyTorch first (500MB vs 2.5GB for GPU build)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Transfer ownership before switching user
RUN chown -R user:user /app

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface

# Pre-download model weights at build time so the container starts immediately
# Kronos-mini + its tokenizer are public models (no HF_TOKEN required)
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('NeoQuasar/Kronos-mini'); \
snapshot_download('NeoQuasar/Kronos-Tokenizer-2k')"

EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
