FROM python:3

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir /usr/src/app/gog_dbs

CMD [ "python", "./gamatrix-gog.py", "-c", "config.yaml" ]