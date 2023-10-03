#!/bin/bash

set -e

# copy environment variables to file so cron can access them
declare -p | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' > /container.env

# run app migrations
python ./manage.py migrate
# collect static assets
python ./manage.py collectstatic --no-input
# discover packages
python ./manage.py discover_packages
# fetch rights statements
python ./manage.py fetch_rights_statements

# start cron
cron

# start Apache
apache2ctl -D FOREGROUND