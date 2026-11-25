FROM python:3.13-slim

WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY index.html index.html

# Database and signing key live on a volume so they survive container rebuilds.
ENV DATABASE_URL=sqlite:////data/mbs_fleet.db \
    SECRET_KEY_FILE=/data/.secret_key \
    FRONTEND_FILE=/srv/index.html
VOLUME /data

WORKDIR /srv/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
