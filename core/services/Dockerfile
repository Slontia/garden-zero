FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-server samba smbclient ca-certificates && rm -rf /var/lib/apt/lists/* && useradd -m -u 1000 -s /bin/sh nas
RUN rm -f /etc/ssh/ssh_host_*
COPY entrypoint.py /app/entrypoint.py
CMD ["python", "/app/entrypoint.py"]
