FROM python:3.11-slim

WORKDIR /usr/src/app



COPY . .
RUN pip install --no-cache-dir . && \
    mkdir /usr/src/app/gog_dbs /usr/src/app/config

CMD [ "python", "-m", "gamatrix", "-c", "/usr/src/app/config/config.yaml" ]