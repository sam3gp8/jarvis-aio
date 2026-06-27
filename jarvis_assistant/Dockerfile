ARG BUILD_FROM
FROM $BUILD_FROM

ARG JARVIS_VERSION=6.26.0

RUN echo "Building JARVIS ${JARVIS_VERSION}" \
    && if ! command -v pip3 >/dev/null 2>&1; then apk add --no-cache py3-pip; fi \
    && pip3 install --no-cache-dir --break-system-packages \
            websockets openai anthropic \
    && echo "pip install complete"

COPY run.sh /run.sh
COPY bootstrap.py /bootstrap.py
COPY jarvis_component/ /jarvis_component/

RUN chmod +x /run.sh

CMD ["/run.sh"]
