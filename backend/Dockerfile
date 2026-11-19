FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# git: coding workspaces are git repositories (run with hooks and external programs disabled)
# fonts-dejavu-core: Unicode font for research report PDFs
# tesseract-ocr + poppler-utils: OCR for images and scanned PDF pages (runs locally, nothing leaves the server)
# Download Debian packages over HTTPS (some networks block plain HTTP on port 80) and retry on flaky links
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do \
      if [ -f "$f" ]; then sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$f"; fi; \
    done; \
    printf 'Acquire::Retries "5";\n' > /etc/apt/apt.conf.d/80-retries; \
    apt-get update; \
    apt-get install -y --no-install-recommends git fonts-dejavu-core tesseract-ocr poppler-utils; \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 aria && mkdir -p /data/uploads /data/workspaces && chown -R aria /data
USER aria
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-keep-alive", "75"]
