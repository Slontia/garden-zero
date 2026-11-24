ARG RCLONE_IMAGE
ARG RESTIC_IMAGE
FROM ${RCLONE_IMAGE} AS rclone
FROM ${RESTIC_IMAGE}
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
ENV RCLONE_CONFIG=/credentials/rclone.conf
ENV RESTIC_CACHE_DIR=/tmp/restic-cache
