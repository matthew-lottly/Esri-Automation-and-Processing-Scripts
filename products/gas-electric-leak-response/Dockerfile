# Starting point only (not built/tested by the author). Mount .env, config/, templates/, state/ and the report shares at run time.
FROM python:3.12-slim
# WeasyPrint (PDF engine) needs Pango and a font
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config ./config
COPY templates ./templates
ENV PYTHONPATH=/app/src
EXPOSE 8080
CMD ["python", "-m", "gas_electric_response", "serve", "--host", "0.0.0.0", "--port", "8080"]
