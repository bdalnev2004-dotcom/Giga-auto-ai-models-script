FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs the aiogram polling loop. Switch to a webhook + gunicorn/uvicorn setup
# later if/when request volume justifies it — polling is fine for a farm this size.
CMD ["python", "bot.py"]
