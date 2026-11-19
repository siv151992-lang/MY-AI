FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Toolchains for the coding workspace (the running container has no internet, so everything is installed here)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git make \
    && rm -rf /var/lib/apt/lists/*
# Libraries available to user code
RUN pip install --no-cache-dir numpy pandas matplotlib sympy scipy pytest
WORKDIR /srv
COPY server.py .
USER nobody
EXPOSE 8800
CMD ["python", "-I", "server.py"]
