# Stage 1: build the wheel
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:f122b730834de5d15a27a18ad07ddc62ec31462600bc1c9361adf6c2215b940d as builder

USER 0
WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN chown -R 1001:0 /build
USER 1001

RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /build/dist

# Stage 2: minimal runtime image
FROM registry.access.redhat.com/ubi9/python-312-minimal@sha256:f122b730834de5d15a27a18ad07ddc62ec31462600bc1c9361adf6c2215b940d

WORKDIR /opt/import-orchestrator

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -rf /tmp/*.whl

COPY tekton/ tekton/

ENV TEKTON_PIPELINE_DIR=/opt/import-orchestrator/tekton

ENTRYPOINT ["import-orchestrator"]
