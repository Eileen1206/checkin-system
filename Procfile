web: python manage.py migrate && python manage.py collectstatic --noinput && (python manage.py createsuperuser --noinput || true) && gunicorn checkin_system.wsgi --log-file -
