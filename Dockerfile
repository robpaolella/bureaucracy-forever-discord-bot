FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py lfg.py recruit.py ./

RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /data \
    && chown -R botuser:botuser /app /data
USER botuser

VOLUME ["/data"]

CMD ["python", "bot.py"]
