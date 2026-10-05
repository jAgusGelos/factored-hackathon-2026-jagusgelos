# Transaction Dispute AI Agent — deployment image (Task 6.1/6.2, AD-2, AD-7).
#
# Built and verified locally this session (docker build + a real restart test);
# not pushed to a registry or platform-deployed — see DEPLOY.md.
#
# AD-2: ships ONLY the sanitized demo fixture + trained classifier pipeline, never
# the full local warehouse (data/warehouse.duckdb) and never AWS credentials — the
# running app has zero AWS dependency by design (proven by
# tests/test_main.py::test_app_serves_with_aws_env_unset).
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# These 3 files must exist locally before `docker build` — produce them with:
#   python etl/extract.py && python etl/build_fixture.py && python etl/train_classifier.py
# (build_fixture needs data/fraud_model.joblib and data/fraud_eval_report.json first:
# python -m etl.train_fraud_model && python -m etl.evaluate_fraud_model, see DEPLOY.md;
# the fraud model itself is never copied into the image.)
# They are gitignored (data/) but not dockerignored (see .dockerignore's explicit
# un-ignore), so `docker build` picks them up straight off local disk. Copied
# before the app/static layers since they change far less often than app code.
COPY data/fixture.duckdb data/demo_users.json data/classifier.joblib ./seed-data/

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

COPY app/ ./app/
COPY static/ ./static/

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
