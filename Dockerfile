# Digest confirmed via trivy scan — see SECURITY-REVIEW.md. Re-verify on any bump.
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

RUN groupadd --gid 1000 lanternpkms \
    && useradd --uid 1000 --gid lanternpkms --create-home --shell /bin/bash lanternpkms

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config

RUN pip install --no-cache-dir .

USER lanternpkms

ENTRYPOINT ["lantern-pkms"]
