FROM node:20-alpine

ARG UID=1000
ARG GID=1000
ARG USER=booktree
ARG GROUPNAME=booktree

ENV USER=${USER}
ENV GROUPNAME=${GROUPNAME}
ENV UID=${UID}
ENV GID=${GID}
ENV BOOKTREE_CONFIG=/config/config.json
ENV BOOKTREE_DB=/config/booktree.db
ENV BOOKTREE_PYTHON=/venv/bin/python
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

WORKDIR /booktree

COPY package.json ./
RUN npm install

COPY . /booktree/
RUN echo "**** installing system packages ****" \
    && apk add --update --no-cache python3 py3-pip ffmpeg \
    && ln -sf python3 /usr/bin/python \
    && mkdir -p /venv \
    && python -m venv /venv \
    && . /venv/bin/activate \
    && pip install --upgrade pip \
    && pip install --no-cache-dir --requirement requirements.txt \
    && npm run build \
    && if getent passwd ${UID} >/dev/null; then deluser "$(getent passwd ${UID} | cut -d: -f1)"; fi \
    && if getent group ${GID} >/dev/null; then delgroup "$(getent group ${GID} | cut -d: -f1)"; fi \
    && addgroup --system --gid ${GID} ${GROUPNAME} \
    && adduser --system --uid ${UID} --disabled-password --gecos "" --ingroup ${GROUPNAME} --no-create-home ${USER} \
    && mkdir -p /config /logs /data \
    && chown -R ${UID}:${GID} /booktree /config /logs /data

USER ${USER}
VOLUME /config
VOLUME /logs
VOLUME /data
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/api/health || exit 1

CMD ["npm", "start"]
