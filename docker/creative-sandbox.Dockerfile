# Owner builds and reviews this image, then configures its immutable sha256 ID.
# The runtime never pulls images or installs dependencies during fulfillment.
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && /usr/bin/node --version
# No credentials, source tree, workspace, volumes or additional runtime env.
LABEL io.widemind.software.runtime="node"
