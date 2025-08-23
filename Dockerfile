FROM --platform=$BUILDPLATFORM python:3.11-slim

# Install ffmpeg, yt-dlp, streamlink
RUN apt-get update && apt-get install -y ffmpeg curl \
    && pip install yt-dlp streamlink \
    && rm -rf /var/lib/apt/lists/*

# Ensure pip-installed scripts are in PATH
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app
COPY app/ /app/

RUN pip install -r requirements.txt

ENV CONTAINER_CONTEXT=true

CMD ["python", "main.py"]