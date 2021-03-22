FROM python:3.9-slim

WORKDIR /usr/src/app

RUN apt update && apt install -y libmagic1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir /usr/src/app/gog_dbs /usr/src/app/config

CMD [ "python", "./gamatrix-gog.py", "-c", "/usr/src/app/config/config.yaml" ]