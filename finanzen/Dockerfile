# syntax=docker/dockerfile:1
# Debian base of Home Assistant (multi-arch, with s6-overlay and bashio).
# The image is fixed here on purpose, so a local build by the Supervisor does not
# switch to the Alpine default base image.
FROM ghcr.io/home-assistant/base-debian:trixie

ARG BUILD_ARCH
ARG BUILD_VERSION

# All Python libraries come from Debian, nothing is compiled during the build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-aiohttp \
        python3-lxml \
        python3-openpyxl \
        python3-paho-mqtt \
    && rm -rf /var/lib/apt/lists/*

COPY app /opt/finanzen
COPY rootfs /
RUN chmod a+x \
        /etc/s6-overlay/s6-rc.d/finanzen/run \
        /etc/s6-overlay/s6-rc.d/finanzen/finish \
    && python3 -c "import aiohttp, lxml, openpyxl, paho.mqtt, sys; sys.path.insert(0, '/opt/finanzen'); import finanzen.web"

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8099/api/state > /dev/null || exit 1

LABEL \
    io.hass.name="Finanzen" \
    io.hass.description="Depot, Fixkosten und Monatsliste" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="${BUILD_VERSION}" \
    org.opencontainers.image.source="https://github.com/Sm0by/ha-addon-finanzen" \
    org.opencontainers.image.version="${BUILD_VERSION}" \
    org.opencontainers.image.licenses="MIT"
