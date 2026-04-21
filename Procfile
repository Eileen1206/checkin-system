release: python manage.py migrate && python manage.py createsuperuser --noinput || true
web: gunicorn checkin_system.wsgi --log-file -
