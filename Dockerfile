# Stage 1: build the wheel
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:fc63cc1bf0e7c1666899e747a995a9e4edf66fa143a6fd9621c6ecd325234dce as builder

USER 0
WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN chown -R 1001:0 /build
USER 1001

RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /build/dist

# Stage 2: minimal runtime image
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:fc63cc1bf0e7c1666899e747a995a9e4edf66fa143a6fd9621c6ecd325234dce

WORKDIR /opt/import-orchestrator

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -rf /tmp/*.whl

COPY tekton/ tekton/

# Git source identity of the embedded pipeline definition. The wheel has no
# .git to query at runtime, so CI bakes these in from the built commit.
ARG PIPELINE_GIT_URL
ARG PIPELINE_GIT_REVISION

ENV TEKTON_PIPELINE_DIR=/opt/import-orchestrator/tekton \
    PIPELINE_GIT_URL=${PIPELINE_GIT_URL} \
    PIPELINE_GIT_REVISION=${PIPELINE_GIT_REVISION}

ENTRYPOINT ["import-orchestrator"]
