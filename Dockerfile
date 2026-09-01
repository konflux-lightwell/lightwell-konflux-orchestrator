# Stage 1: build the wheel
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:566d836a1fe3a8eed540c71b8e438919b897bf2294da5f9d682d589553ab8434 as builder

USER 0
WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN chown -R 1001:0 /build
USER 1001

RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /build/dist

# Stage 2: minimal runtime image
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:566d836a1fe3a8eed540c71b8e438919b897bf2294da5f9d682d589553ab8434

WORKDIR /opt/import-orchestrator

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -rf /tmp/*.whl

COPY tekton/ tekton/

ENV TEKTON_PIPELINE_DIR=/opt/import-orchestrator/tekton

ENTRYPOINT ["import-orchestrator"]
