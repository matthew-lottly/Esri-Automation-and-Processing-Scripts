# Starting point only (not built/tested by the author). Mount .env, config/, state/ and report shares at run time.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config ./config
ENV PYTHONPATH=/app/src
EXPOSE 8080
CMD ["python", "-m", "gas_electric_response", "serve", "--host", "0.0.0.0", "--port", "8080"]
