# Trusted HPC gateway runtime image.
#
# This image ships the trusted core (job contract, gateway, adapters,
# conformance). It carries no case images and no site secrets — those arrive
# as mounts/env at run time, owned by the site. The HTTP binding for the
# gateway is wired in Task 12; for now the image proves the package installs.
FROM python:3.12-slim

WORKDIR /opt/mlip-bench

COPY pyproject.toml ./
COPY ccbench ./ccbench
COPY schemas ./schemas

RUN pip install --no-cache-dir .

CMD ["python", "-c", "import ccbench.hpc.gateway_runtime as r; print('hpc-gateway runtime installed:', r.__file__)"]
