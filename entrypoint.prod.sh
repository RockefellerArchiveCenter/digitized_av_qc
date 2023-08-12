#!/bin/bash

set -e

python ./manage.py migrate
python ./manage.oy collectstatic

apache2ctl -D FOREGROUND