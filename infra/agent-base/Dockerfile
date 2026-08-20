FROM docker.io/library/buildpack-deps:jammy@sha256:c8a8416c0a50c43da83ce229c3d1ce3a19b81b9b72f262f1c90d3d5980c33c7c

ARG AIDER_VERSION=0.86.2
ARG PYTHON_VERSION=3.12.8
ARG JDK_SHA256=f2dc5418092c43003db8f9005c4a286e1c0104fea96ccdd49e8ebd037cac9219
ARG GRADLE_SHA256=544c35d6bd849ae8a5ed0bcea39ba677dc40f49df7d1835561582da2009b961d
ARG GRADLE_CACHE_SHA256

LABEL io.haifa.evals.image-purpose="coding-agent-infrastructure" \
      io.haifa.evals.aider-version="${AIDER_VERSION}" \
      io.haifa.evals.java-version="21.0.8+9" \
      io.haifa.evals.gradle-version="8.7" \
      io.haifa.evals.gradle-cache-sha256="${GRADLE_CACHE_SHA256}" \
      io.haifa.evals.contains-task-data="false"

COPY temurin-jdk.tar.gz /tmp/temurin-jdk.tar.gz
COPY gradle-8.7-bin.zip /tmp/gradle-8.7-bin.zip
COPY gradle-modules-2/ /root/.gradle/caches/modules-2/
COPY aider-requirements.lock /tmp/aider-requirements.lock
COPY verify_aider_lock.py /tmp/verify_aider_lock.py
ADD aider-runtime.tar.gz /

RUN set -eux; \
    echo "${JDK_SHA256}  /tmp/temurin-jdk.tar.gz" | sha256sum -c -; \
    install -d -m 0755 /opt/haifa/java; \
    tar -xzf /tmp/temurin-jdk.tar.gz -C /opt/haifa/java --strip-components=1; \
    rm /tmp/temurin-jdk.tar.gz; \
    /opt/haifa/java/bin/java -version; \
    /opt/haifa/java/bin/java --list-modules | grep -q '^jdk.random@'

RUN set -eux; \
    echo "${GRADLE_SHA256}  /tmp/gradle-8.7-bin.zip" | sha256sum -c -; \
    GRADLE_DIST=/root/.gradle/wrapper/dists/gradle-8.7-bin/bhs2wmbdwecv87pi65oeuq5iu; \
    install -d -m 0755 "${GRADLE_DIST}"; \
    mv /tmp/gradle-8.7-bin.zip "${GRADLE_DIST}/gradle-8.7-bin.zip"; \
    chmod 0444 "${GRADLE_DIST}/gradle-8.7-bin.zip"

RUN set -eux; \
    AIDER_HOME=/root/.local/share/uv/tools/aider-chat; \
    test "$("${AIDER_HOME}/bin/python" --version)" = "Python ${PYTHON_VERSION}"; \
    "${AIDER_HOME}/bin/python" /tmp/verify_aider_lock.py /tmp/aider-requirements.lock; \
    rm /tmp/aider-requirements.lock /tmp/verify_aider_lock.py; \
    test "$("${AIDER_HOME}/bin/aider" --version)" = "aider ${AIDER_VERSION}"; \
    install -d -m 0755 /root/.local/bin; \
    ln -sf "${AIDER_HOME}/bin/aider" /root/.local/bin/aider; \
    printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"' > /root/.local/bin/env; \
    chmod 0644 /root/.local/bin/env; \
    ln -s "${AIDER_HOME}/bin/aider" /usr/local/bin/aider

ENV JAVA_HOME=/opt/haifa/java
ENV PATH=/opt/haifa/java/bin:/root/.local/bin:${PATH}

RUN set -eux; \
    printf '%s\n' \
      'schema_version=1' \
      'aider_version=0.86.2' \
      'java_version=21.0.8+9' \
      'gradle_version=8.7' \
      'contains_task_data=false' \
      > /opt/haifa/agent-infra.properties; \
    chmod 0444 /opt/haifa/agent-infra.properties; \
    test ! -e /app; \
    test ! -e /opt/haifa/haifa-agent.jar

WORKDIR /app
