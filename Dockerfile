FROM python:3.10-slim as base

ENV PYTHONUNBUFFERED 1
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
ADD . /code/

FROM base as test
COPY test_requirements.txt .coveragerc ./
RUN pip install -r test_requirements.txt

FROM base as build
EXPOSE 8080