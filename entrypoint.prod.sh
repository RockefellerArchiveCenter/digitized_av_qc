#!/bin/bash

set -e

# copy environment variables to file so cron can access them
declare -p | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' > /container.env
# run app migrations
python ./manage.py migrate
# collect static assets
python ./manage.py collectstatic --no-input
# start cron
cron

# run crons to pull in data
python ./manage.py runcrons

# start Apache
apache2ctl -D FOREGROUND