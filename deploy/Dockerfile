FROM python:3.12-slim-bookworm
WORKDIR /app
COPY workflow/requirements.txt requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY workflow workflow
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["uvicorn", "workflow.server:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "30", "--timeout-graceful-shutdown", "20", "--no-access-log"]
