# The signature API. Built by compose.yaml.
FROM python:3.13-slim
RUN pip install --no-cache-dir psycopg2-binary==2.9.10 \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin asra
COPY server/ /app/
USER asra
WORKDIR /app
ENV PYTHONUNBUFFERED=1 ASRA_HOST=0.0.0.0 ASRA_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
CMD ["python", "/app/asra_server.py"]
