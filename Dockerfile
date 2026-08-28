FROM python:3.12-slim

WORKDIR /app

# ffmpeg/ffprobe drive services/uniquify_service.py — the slim image has neither.
# fonts-dejavu-core provides /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf,
# which services/editor_service.py points drawtext at explicitly — without a font
# package, fontconfig has nothing to find and the hook-text/subtitle render dies
# outright (confirmed: same failure on a stock Windows ffmpeg build too).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs the aiogram polling loop. Switch to a webhook + gunicorn/uvicorn setup
# later if/when request volume justifies it — polling is fine for a farm this size.
CMD ["python", "bot.py"]
