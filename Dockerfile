FROM python:2.7.13

# No apt-get: the jessie package indexes are gone. gcc, git and libpq-dev
# are already present in the buildpack-deps base layers.
WORKDIR /app
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-dev.txt

COPY docker/app/entrypoint.sh /app/docker/app/entrypoint.sh
ENTRYPOINT ["docker/app/entrypoint.sh"]
CMD ["mpctl", "--help"]
