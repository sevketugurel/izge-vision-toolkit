FROM ghcr.io/astral-sh/uv:0.8.4 AS uv
FROM python:3.12.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/izge
COPY experiments/rfdetr_training/pyproject.toml experiments/rfdetr_training/uv.lock /opt/training/
RUN uv sync --locked --project /opt/training --python /usr/local/bin/python
COPY backend/izge /opt/izge/backend/izge
COPY experiments/rfdetr_training /opt/izge/experiments/rfdetr_training
ENV PYTHONPATH=/opt/izge/backend
ENTRYPOINT ["/opt/training/.venv/bin/python", "experiments/rfdetr_training/train.py"]
