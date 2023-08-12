#!/bin/bash

# Apply database migrations
./wait-for-it.sh ${SQL_HOST}:${SQL_PORT} -- echo "Apply database migrations"
python manage.py migrate

#Start server
echo "Starting server"
python manage.py runserver 0.0.0.0:${APPLICATION_PORT}