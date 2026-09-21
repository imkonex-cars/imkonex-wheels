FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json ./
COPY scripts/build.mjs ./scripts/build.mjs
COPY frontend ./frontend
COPY data ./data
RUN node scripts/build.mjs

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SERVE_FRONTEND=true
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser
COPY backend ./backend
COPY data ./data
COPY --from=frontend /app/dist ./dist
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5)"
CMD ["uvicorn","backend.app:app","--host","0.0.0.0","--port","8000","--workers","2"]
