FROM python:3.11-alpine@sha256:d9368b3a5ac59afea7b5d4f2e2aea0941dbf9fdee9c369c5bec00b98244bc929
WORKDIR /app
COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev openssl-dev python3-dev && pip install --no-cache-dir -r requirements.txt && apk del .build-deps
COPY main.py .
USER 10001:10001
CMD ["python3", "main.py"]
