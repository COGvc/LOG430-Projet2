# Contexte de build : racine du dépôt (pour copier la bibliothèque commune).
FROM python:3.12-slim
WORKDIR /srv
COPY common/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/candronex_common ./candronex_common
COPY services/service-ordering-service/app ./app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --workers ${WORKERS:-1}"]
