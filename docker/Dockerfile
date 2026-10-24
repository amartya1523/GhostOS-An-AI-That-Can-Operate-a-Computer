# GhostOS server + dashboard in one image.
FROM node:22-slim AS dashboard
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /app
COPY pyproject.toml README.md ./
COPY ghostos/ ghostos/
COPY fixtures/ fixtures/
RUN pip install --no-cache-dir . && python -m playwright install chromium
COPY --from=dashboard /app/dashboard/out dashboard/out
ENV GHOSTOS_HOME=/data GHOSTOS_SANDBOX=process
EXPOSE 8765
CMD ["ghost", "serve", "--host", "0.0.0.0", "--port", "8765"]
