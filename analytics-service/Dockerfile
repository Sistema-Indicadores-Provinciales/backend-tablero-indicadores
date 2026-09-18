FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home analytics
COPY app ./app
COPY main.py ./
COPY data ./data
RUN mkdir -p /app/storage /app/uploads && chown -R analytics:analytics /app
USER analytics
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "12"]
