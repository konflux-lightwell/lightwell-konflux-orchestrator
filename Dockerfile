# Stage 1: build the wheel
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:3d88f8206b92cff1407dea785267fa458140961f6c0d53328a652100f7332303 as builder

USER 0
WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN chown -R 1001:0 /build
USER 1001

RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /build/dist

# Stage 2: minimal runtime image
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:3d88f8206b92cff1407dea785267fa458140961f6c0d53328a652100f7332303

WORKDIR /opt/import-orchestrator

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -rf /tmp/*.whl

COPY tekton/ tekton/

ENV TEKTON_PIPELINE_DIR=/opt/import-orchestrator/tekton

ENTRYPOINT ["import-orchestrator"]
