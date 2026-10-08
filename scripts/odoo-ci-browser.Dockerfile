ARG ODOO_IMAGE=docker.io/library/odoo@sha256:f54272f31d5f77e4146b887efb3761c98480317daf687e4b4b5e76ed8bcc08c5
FROM ${ODOO_IMAGE}

USER root
COPY chrome-linux64.zip /tmp/chrome-linux64.zip
RUN set -eux; \
    mkdir -p /opt/chrome-for-testing; \
    python3 -c 'import zipfile; zipfile.ZipFile("/tmp/chrome-linux64.zip").extractall("/opt/chrome-for-testing")'; \
    chmod 0755 \
        /opt/chrome-for-testing/chrome-linux64/chrome \
        /opt/chrome-for-testing/chrome-linux64/chrome-wrapper \
        /opt/chrome-for-testing/chrome-linux64/chrome_crashpad_handler \
        /opt/chrome-for-testing/chrome-linux64/chrome_sandbox; \
    sed -ri 's|http://(archive|security).ubuntu.com|https://\\1.ubuntu.com|g' \
        /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 update; \
    while IFS= read -r dependency; do \
        [ -z "$dependency" ] || apt-get \
            -o Acquire::Retries=5 \
            -o Acquire::https::Timeout=30 \
            satisfy -y --no-install-recommends "$dependency"; \
    done < /opt/chrome-for-testing/chrome-linux64/deb.deps; \
    test -x /opt/chrome-for-testing/chrome-linux64/chrome; \
    /opt/chrome-for-testing/chrome-linux64/chrome --version; \
    rm -f /tmp/chrome-linux64.zip; \
    rm -rf /var/lib/apt/lists/*

ENV ODOO_BROWSER_BIN=/opt/chrome-for-testing/chrome-linux64/chrome
USER odoo
