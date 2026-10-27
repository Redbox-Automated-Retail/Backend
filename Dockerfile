# One image for every Redbox backend service; the container command picks the service.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends xdelta3 tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY redbox_backend ./redbox_backend
COPY tests ./tests

VOLUME ["/data", "/certs"]
CMD ["python", "-m", "redbox_backend", "--help"]
