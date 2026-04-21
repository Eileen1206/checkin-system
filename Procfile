release: python manage.py migrate && python manage.py collectstatic --noinput
web: gunicorn checkin_system.wsgi --log-file -
