# Minimal forward-only sidecar. Its base digest is supplied by the operator's
# qualification build; this image contains no credentials and no model client.
ARG BASE_IMAGE=python:3.12-alpine
FROM ${BASE_IMAGE}
RUN addgroup -S gateway -g 10002 && adduser -S gateway -u 10002 -G gateway
USER 10002:10002
CMD ["sleep", "3600"]
