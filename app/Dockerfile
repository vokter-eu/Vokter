FROM python:3.12-slim
WORKDIR /app

# ffmpeg: faster-whisper needs it to decode WebM/OGG audio from the browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN useradd -m -u 1000 vokter && mkdir -p /data && chown vokter:vokter /data
USER vokter

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
