FROM docker:29-cli@sha256:1a4c7cb63513f349bdad01fcc6e0f3f2f67d37b9da86f14dc0d4a0942eecda00 AS docker-cli
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG ENGINE_REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ENGINE_REVISION=${ENGINE_REVISION}
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 autocheck && useradd -u 10001 -g 10001 -M autocheck
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /opt/element-test
COPY deploy/autocheck/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
COPY pyproject.toml README.md ./
COPY element_test ./element_test
COPY element_check_service ./element_check_service
COPY assignments/programming-practice ./assignments/programming-practice
COPY src ./src
COPY bin ./bin
COPY config ./config
COPY docs/grading-v1.schema.json docs/autocheck-openapi.yaml ./docs/
COPY deploy/autocheck/service.yaml ./deploy/autocheck/service.yaml
RUN pip install --no-deps . && chmod +x bin/script-runtime bin/element-test
USER 10001:10001
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "element_check_service", "api", "--host", "0.0.0.0", "--port", "8000"]
