FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Dependencies first so code edits do not invalidate this layer.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000
# Build the database on first start if `make data` has not been run on the host. With no internet
# and WEATHER_SOURCE=auto this falls back to the synthetic climatology (flagged in /health).
CMD ["sh", "-c", "[ -f \"$DB_PATH\" ] || python -m data.build; exec uvicorn api.main:app --host 0.0.0.0 --port 8000"]
