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

USER shadowagent
EXPOSE 10000

# --proxy-headers trusts X-Forwarded-Proto from Render's edge (the only thing
# that can reach this container), so request.url.scheme reflects the
# browser's real HTTPS connection instead of the plain HTTP Render forwards
# internally -- this is what makes the session cookie's Secure flag correct.
CMD ["uvicorn", "shadow.app:app", "--host", "0.0.0.0", "--port", "10000", "--proxy-headers", "--forwarded-allow-ips=*"]
