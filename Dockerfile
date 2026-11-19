FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot/ .

RUN mkdir -p /app/data

EXPOSE 7860

CMD ["python", "main.py"]