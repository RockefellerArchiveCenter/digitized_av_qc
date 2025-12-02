#!/bin/sh

set -e

# run app migrations
python ./manage.py migrate
# collect static assets
python ./manage.py collectstatic --no-input
# fetch rights statements
python ./manage.py fetch_rights_statements

# start cron
crond -b

# start Apache
httpd -D FOREGROUND