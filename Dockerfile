FROM python:3.11-bookworm as base
ARG WSGI_VERSION=5.0.0

RUN apt-get clean && apt-get update
RUN apt-get install --yes ffmpeg

COPY requirements.txt /var/www/digitized-av-qc/requirements.txt
WORKDIR /var/www/digitized-av-qc
RUN pip install -r requirements.txt
COPY . /var/www/digitized-av-qc

FROM base as build
RUN apt-get install --yes apache2 apache2-dev python3.11-dev cron
RUN wget https://github.com/GrahamDumpleton/mod_wsgi/archive/refs/tags/${WSGI_VERSION}.tar.gz \
    && tar xvfz ${WSGI_VERSION}.tar.gz \
    && cd mod_wsgi-${WSGI_VERSION} \
    && ./configure --with-apxs=/usr/bin/apxs --with-python=/usr/local/bin/python \
    && make \
    && make install \
    && make clean
RUN rm -rf ${WSGI_VERSION}.tar.gz mod_wsgi-${WSGI_VERSION}

ADD ./apache/000-digitized_av_qc.conf /etc/apache2/sites-available/000-digitized_av_qc.conf
ADD ./apache/wsgi.load /etc/apache2/mods-available/wsgi.load
RUN a2dissite 000-default.conf
RUN a2ensite 000-digitized_av_qc.conf
RUN a2enmod headers
RUN a2enmod rewrite
RUN a2enmod wsgi

COPY crontab /etc/cron.d/crontab
RUN crontab /etc/cron.d/crontab

EXPOSE 80
ENTRYPOINT [ "./entrypoint.prod.sh" ]