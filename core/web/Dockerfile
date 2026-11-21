ARG NAS_WEB_BASE_IMAGE=python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
FROM ${NAS_WEB_BASE_IMAGE}
WORKDIR /app
COPY app.py connect.js meta.css dashboard.html dashboard.js system.html system.js logs.html logs.js /app/
USER 1000:0
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python", "/app/app.py"]
