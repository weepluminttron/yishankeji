FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

ENV HOST=0.0.0.0
ENV PORT=8017
ENV PYTHONUNBUFFERED=1

EXPOSE 8017

CMD ["python", "server.py"]
