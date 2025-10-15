FROM python:3.11-alpine AS base

ENV APPLICATION_NAME=digitized-av-qc
ENV APPLICATION_DIR=digitized_av_qc
ENV APPLICATION_PORT=80

# Install base system requirements
RUN apk add --no-cache ffmpeg postgresql-dev

WORKDIR /var/www/${APPLICATION_NAME}

# Install Python requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bring in the rest of the application
COPY ${APPLICATION_DIR} package_review entrypoint.* manage.py ./

FROM base AS build

# Install webserver requirements
RUN apk add --no-cache apache2 apache2-dev apache2-mod-wsgi

# Disable existing sites
RUN find /etc/apache2/conf.d/ -type f -name "*.conf" -print0 | xargs -0 -I {} mv {} {}.disabled

# Enable WSGI
RUN mv /etc/apache2/conf.d/wsgi-module.conf.disabled /etc/apache2/conf.d/wsgi-module.conf

# Create the default site
COPY ./apache/${APPLICATION_NAME}.conf /etc/apache2/conf.d/${APPLICATION_NAME}.conf

# Add cron schedule
COPY crontab /etc/crontabs/root

# Expose HTTP port
EXPOSE ${APPLICATION_PORT}

ENTRYPOINT ["./entrypoint.prod.sh"]
