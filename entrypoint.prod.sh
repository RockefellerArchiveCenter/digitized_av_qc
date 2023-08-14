#!/bin/bash

set -e

python ./manage.py migrate
python ./manage.py collectstatic --no-input
cron

apache2ctl -D FOREGROUND