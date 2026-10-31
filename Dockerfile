# One image for every Redbox backend service; the container command picks the service.

# The admin dashboard's frontend (dashboard-ui: Vite + React), built into redbox_backend/dashboard/static.
FROM node:22-alpine AS ui
WORKDIR /src/dashboard-ui
COPY dashboard-ui/package.json dashboard-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard-ui/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends xdelta3 tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# vistadb3 (git submodule vendor/vistadb3-py): pure-Python VistaDB 3.5 reader/writer for the kiosk catalog
COPY vendor/vistadb3-py/src/vistadb3 ./vistadb3
COPY redbox_backend ./redbox_backend
COPY --from=ui /src/redbox_backend/dashboard/static ./redbox_backend/dashboard/static
COPY tests ./tests

VOLUME ["/data", "/certs"]
CMD ["python", "-m", "redbox_backend", "--help"]
