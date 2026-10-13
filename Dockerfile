FROM ubuntu:26.04 AS compiler
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends gcc-multilib make curl ca-certificates unzip && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fL --retry 3 https://codeload.github.com/sympower/phoneme-preverifier/zip/655d18803d8eb34c480fcff7b571cbf5f07cee2e -o source.zip \
 && echo '6bc0dc95fed31a781aec71ed3edb201c4180ac298c9b213ee8f8f91909eb0ff3  source.zip' | sha256sum -c - \
 && unzip -q source.zip \
 && cd phoneme-preverifier-*/build/linux && make clean all EXTRACFLAGS='-std=gnu89 -fcommon' \
 && cp preverify /build/preverify
RUN curl -fL --retry 3 https://cdn.azul.com/zulu/bin/zulu6.22.0.3-ca-jdk6.0.119-linux_x64.tar.gz -o jdk.tar.gz \
 && echo 'dd4765b45d9f1fc319a6e5be0bbc79371582daa8fd77872d51072f16deca2625  jdk.tar.gz' | sha256sum -c - \
 && mkdir /build/java6 && tar xzf jdk.tar.gz -C /build/java6 --strip-components=1

FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    BB_JAVA=/opt/java6/bin/java BB_TOOLS_ROOT=/tools BB_DATA_DIR=/data \
    BB_NATIVE_PICKER=0 BB_WEB_HOST=0.0.0.0 BB_WEB_PORT=5001 \
    BB_CONFIG_FILE=/run/secrets/builder.env PATH=/opt/bin:/opt/java6/bin:/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home builder && mkdir /data && chown builder:builder /data
COPY --from=compiler /build/java6 /opt/java6
COPY --from=compiler /build/preverify /opt/bin/preverify
# Keep corresponding GPL source and copyright notices for the compiled tool.
COPY --from=compiler /build/source.zip /usr/share/doc/preverify/source.zip
COPY --from=compiler /usr/share/common-licenses/GPL-2 /usr/share/doc/preverify/COPYING
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt
COPY server.py legacy.py artifacts.py picker.py app.js index.html ./
USER builder
EXPOSE 5001
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import socket;s=socket.create_connection(('127.0.0.1',5001),3);s.close()"
CMD ["python", "server.py"]
