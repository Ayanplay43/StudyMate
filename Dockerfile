FROM python:3.12-slim
WORKDIR /app
COPY server.py init_db.py schema.sql start_render.sh ./
COPY static/ ./static/
RUN chmod 755 /app/start_render.sh
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=10000 STUDYMATE_DB=/data/studymate.sqlite
EXPOSE 10000
CMD ["/bin/sh", "/app/start_render.sh"]
