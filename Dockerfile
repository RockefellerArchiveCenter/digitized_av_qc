FROM python:3.10-buster as base

ENV PYTHONUNBUFFERED 1
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
ADD . /code/

FROM base as build
EXPOSE 8080