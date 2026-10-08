FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system shadowagent && adduser --system --ingroup shadowagent shadowagent

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shadow ./shadow
COPY scripts ./scripts
COPY tests ./tests
COPY README.md ./README.md

USER shadowagent
EXPOSE 10000

CMD ["uvicorn", "shadow.app:app", "--host", "0.0.0.0", "--port", "10000"]
