FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim

RUN useradd --system --uid 10001 --create-home --home-dir /var/lib/mercswitch mercswitch
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels

USER mercswitch
WORKDIR /var/lib/mercswitch
VOLUME ["/var/lib/mercswitch"]
EXPOSE 2222/tcp 1161/udp
ENTRYPOINT ["mercswitchd"]
CMD ["run", "--config", "/etc/mercswitch/mercswitchd.toml"]

