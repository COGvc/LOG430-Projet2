FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 5002
CMD ["flask", "--app", "app.main", "run", "--host=0.0.0.0", "--port=5002"]
