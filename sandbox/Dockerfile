FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Libraries available to user code (installed at build time; the running container has no internet)
RUN pip install --no-cache-dir numpy pandas matplotlib sympy scipy
WORKDIR /srv
COPY server.py .
USER nobody
EXPOSE 8800
CMD ["python", "-I", "server.py"]
