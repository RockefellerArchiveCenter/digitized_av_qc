#!/bin/bash

# Apply database migrations
if [ ! -f manage.py ]; then
  cd digitized_av_qc
fi

./wait-for-it.sh db:5432 -- echo "Apply database migrations"
python manage.py migrate

#Start server
echo "Starting server"
python manage.py runserver 0.0.0.0:${APPLICATION_PORT}