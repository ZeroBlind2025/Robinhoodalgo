# ---------------------------------------------------------------------
# Stage 1: build the React/Vite dashboard
# ---------------------------------------------------------------------
FROM node:20-alpine AS dashboard-build

# Pass dashboard config through as build args. Vite inlines any
# VITE_* env var at build time, so these end up baked into the
# compiled bundle. Pass them from Railway via the service's Build
# Args setting (Settings -> Build -> Build Args), e.g.:
#     VITE_API_TOKEN=<same value as DASHBOARD_TOKEN>
#
# If DASHBOARD_TOKEN is unset on the engine side, leave VITE_API_TOKEN
# blank here too — the dashboard will make unauthenticated requests.
ARG VITE_API_URL=""
ARG VITE_API_TOKEN=""
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_API_TOKEN=$VITE_API_TOKEN

WORKDIR /dashboard

# Copy only package manifests first so Docker caches the npm install
# layer across subsequent builds.
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest of the dashboard sources and build the static bundle.
COPY dashboard/ ./
RUN npm run build

# ---------------------------------------------------------------------
# Stage 2: Python runtime for the trading engine + status server
# ---------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the engine source.
COPY . .

# Drop in the dashboard build from stage 1. server.py will auto-mount
# it as static files if the directory exists.
COPY --from=dashboard-build /dashboard/dist ./dashboard/dist

# Railway injects PORT at runtime; server.py reads config.SERVER_PORT
# which falls back to PORT or 8000.
EXPOSE 8000

CMD ["python", "main.py"]
