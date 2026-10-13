FROM python:3.11-alpine@sha256:d9368b3a5ac59afea7b5d4f2e2aea0941dbf9fdee9c369c5bec00b98244bc929
ENV MUSL_LOCPATH=/usr/share/i18n/locales/musl TZ=Europe/Moscow
WORKDIR /app
RUN apk --no-cache add musl-locales musl-locales-lang tzdata
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py telegram_bot.py telegram_send.py cron_builder.py ./
COPY tests ./tests
CMD ["python", "/app/telegram_bot.py"]
