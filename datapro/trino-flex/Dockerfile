# Custom Trino image with the DataPro Flex connector baked in.
#
# trinodb/trino:latest is a stripped RHEL UBI without a package manager
# or Python. Rather than install one, we copy the Python 3.12 runtime
# + the datapro_flex venv across from python:3.12-slim using a multi-
# stage build. Python's stdlib + pyarrow + grpcio are all glibc-linked
# binaries that work fine across the Debian-slim ↔ RHEL UBI boundary
# (UBI has a newer glibc; backwards-compatible).
#
# Two stages:
#   pybuild  — builds the venv on a vanilla python:3.12-slim
#   final    — Trino image + the copied Python tree + flex JARs
#
# Build context for this Dockerfile is HMS/ (the repo root), pruned by
# `HMS/.dockerignore` to just the files the COPY lines reference.

# ---- stage 1: build the Python venv ------------------------------------

# Fully-qualified so hosts pulling through a registry proxy (Artifactory's
# docker-remote, etc.) resolve it the same as a direct Docker Hub pull.
FROM docker.io/library/python:3.12-slim AS pybuild

# Redirect pip at an internal mirror on networks where pypi.org is blocked.
# Public defaults are a no-op for everyone else. Pass at build time, e.g.
#   --build-arg PIP_INDEX_URL=http://host:8081/artifactory/api/pypi/repo/simple
#   --build-arg PIP_TRUSTED_HOST=host   (needed for plain-HTTP / TLS-intercept mirrors)
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=""
# Exported to ENV so every `pip install` in this stage inherits them.
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

# Build the venv at the exact path it will live in the final image so
# the venv's shebangs + sys.prefix all resolve correctly.
RUN python3 -m venv /opt/flex-venv \
    && /opt/flex-venv/bin/pip install --no-cache-dir --upgrade pip

# Layer cache: install deps before the package itself so iterating on
# the package doesn't rebuild pyarrow.
RUN /opt/flex-venv/bin/pip install --no-cache-dir 'pyarrow>=18.1,<19' 'grpcio>=1.66'

COPY flex/runtime /tmp/flex-runtime
RUN /opt/flex-venv/bin/pip install --no-cache-dir /tmp/flex-runtime

# ---- stage 2: final Trino image with Python + flex plugin --------------

FROM docker.io/trinodb/trino:latest

USER root

# Bring across the system Python interpreter, its stdlib, and the
# shared library (libpython3.12.so) so the interpreter can actually link.
# python:3.12-slim puts the interpreter binary in /usr/local/bin,
# the stdlib in /usr/local/lib/python3.12, and the .so in /usr/local/lib.
COPY --from=pybuild /usr/local/lib/libpython3.12.so* /usr/local/lib/
COPY --from=pybuild /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=pybuild /usr/local/bin/python3.12 /usr/local/bin/python3.12
# Refresh the linker cache so the new .so is discoverable, then symlink
# the unversioned name + sanity-check the interpreter loads cleanly.
RUN ldconfig \
    && ln -s /usr/local/bin/python3.12 /usr/local/bin/python3 \
    && /usr/local/bin/python3 -c "print('python ok')"

# The venv (with pyarrow + grpcio + datapro_flex installed) goes at the
# exact path the FlexConnectorFactory's default `flex.python` property
# resolves to: /opt/flex-venv/bin/python3.
COPY --from=pybuild /opt/flex-venv /opt/flex-venv
RUN /opt/flex-venv/bin/python3 -c "import datapro_flex; import pyarrow.flight; print('flex runtime ok')"

# Arrow's off-heap memory needs reflective access to java.nio. Without
# these `--add-opens` flags the Flight client throws InaccessibleObjectException
# the first time it tries to read a record batch off the wire.
# (Per Arrow's Java install guide: https://arrow.apache.org/docs/java/install.html)
RUN { \
    echo "--add-opens=java.base/java.nio=ALL-UNNAMED"; \
    echo "--add-opens=java.base/java.lang=ALL-UNNAMED"; \
    echo "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED"; \
    echo "--sun-misc-unsafe-memory-access=allow"; \
} >> /etc/trino/jvm.config

# Trino plugin directory: connector JAR + runtime deps, all in one
# directory. Trino's plugin loader walks this directory and instantiates
# every JAR's `Plugin` SPI entry in an isolated classloader.
COPY flex/connector/target/trino-flex-connector-0.1.0.jar /usr/lib/trino/plugin/flex/
COPY flex/connector/target/plugin/ /usr/lib/trino/plugin/flex/

USER trino
