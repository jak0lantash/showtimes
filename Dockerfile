FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8080 XDG_CACHE_HOME=/tmp/cache TZ=Europe/Dublin
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY showtimes.py web.py ./
RUN useradd --system --no-create-home app
USER app
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)"
CMD ["python3", "web.py"]
