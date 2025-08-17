FROM --platform=linux/amd64 python:3.11-slim

# Install ffmpeg, yt-dlp, streamlink
RUN apt-get update && apt-get install -y ffmpeg curl \
    && pip install yt-dlp streamlink \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/ /app/

RUN pip install -r requirements.txt

VOLUME ["/data", "/channels.txt"]

CMD ["python", "main.py"]