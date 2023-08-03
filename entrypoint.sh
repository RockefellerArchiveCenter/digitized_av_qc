#!/bin/bash

# Apply database migrations
if [ ! -f manage.py ]; then
  cd digitized_av_qc
fi

# Create config.py if it doesn't exist
if [ ! -f digitized_av_qc/config.py ]; then
    echo "Creating config file"
    cp digitized_av_qc/config.py.example digitized_av_qc/config.py
fi

./wait-for-it.sh db:5432 -- echo "Apply database migrations"
python manage.py migrate

#Start server
echo "Starting server"
python manage.py runserver 0.0.0.0:${APPLICATION_PORT}