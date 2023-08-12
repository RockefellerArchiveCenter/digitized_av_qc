#!/bin/bash

set -e

python ./manage.py migrate
python ./manage.py collectstatic

apache2ctl -D FOREGROUND