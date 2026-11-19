FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Toolchains for the coding workspace (the running container has no internet, so everything is installed here)
# Download Debian packages over HTTPS (some networks block plain HTTP on port 80) and retry on flaky links
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do \
      if [ -f "$f" ]; then sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$f"; fi; \
    done; \
    printf 'Acquire::Retries "5";\n' > /etc/apt/apt.conf.d/80-retries; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs npm git make; \
    rm -rf /var/lib/apt/lists/*
# Libraries available to user code
RUN pip install --no-cache-dir numpy pandas matplotlib sympy scipy pytest
WORKDIR /srv
COPY server.py .
USER nobody
EXPOSE 8800
CMD ["python", "-I", "server.py"]
