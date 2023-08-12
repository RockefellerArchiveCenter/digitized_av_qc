FROM python:3.10-buster as base

COPY . /var/www/digitized-av-qc
WORKDIR /var/www/digitized-av-qc
RUN pip install -r requirements.txt

FROM base as build
RUN apt-get update
RUN apt-get install --yes apache2 apache2-dev
RUN wget https://github.com/GrahamDumpleton/mod_wsgi/archive/refs/tags/4.9.0.tar.gz \
    && tar xvfz 4.9.0.tar.gz \
    && cd mod_wsgi-4.9.0 \
    && ./configure --with-apxs=/usr/bin/apxs --with-python=/usr/local/bin/python \
    && make \
    && make install \
    && make clean
RUN rm -rf 4.9.0.tar.gz mod_wsgi-4.9.0

ADD ./apache/000-digitized_av_qc.conf /etc/apache2/sites-available/000-digitized_av_qc.conf
ADD ./apache/wsgi.load /etc/apache2/mods-available/wsgi.load
RUN a2dissite 000-default.conf
RUN a2ensite 000-digitized_av_qc.conf
RUN a2enmod headers
RUN a2enmod rewrite
RUN a2enmod wsgi

EXPOSE 80
ENTRYPOINT [ "./entrypoint.prod.sh" ]