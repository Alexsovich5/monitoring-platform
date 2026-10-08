FROM ubuntu:14.04

ENV DEBIAN_FRONTEND=noninteractive

# The 2.4.3 packages are no longer in the repository index (it only lists
# the newest 2.4.x), so "make zabbix-vendor" downloads them by exact file
# name over HTTPS into vendor/, and they are checked here against the
# SHA-256 sums committed in SHA256SUMS before dpkg sees them.  Their
# dependencies come from the trusty archive through apt, which verifies
# the archive's signed Release files.  dbconfig-common is installed first
# and told not to touch a database during the build (the server package's
# dbconfig answer is also written to its dbconfig-common file, which
# dbconfig-common reads back over the debconf value); the entrypoint loads
# the schema.
COPY SHA256SUMS /tmp/debs/SHA256SUMS
COPY vendor/ /tmp/debs/
RUN cd /tmp/debs && sha256sum -c SHA256SUMS \
 && for i in 1 2 3; do apt-get update && break; sleep 5; done \
 && for i in 1 2 3; do apt-get install -y --no-install-recommends dbconfig-common && break; sleep 5; done \
 && echo 'zabbix-server-pgsql zabbix-server-pgsql/dbconfig-install boolean false' | debconf-set-selections \
 && echo 'zabbix-frontend-php zabbix-frontend-php/dbconfig-install boolean false' | debconf-set-selections \
 && mkdir -p /etc/dbconfig-common \
 && printf "dbc_install='false'\ndbc_dbtype='pgsql'\n" > /etc/dbconfig-common/zabbix-server-pgsql.conf \
 && (dpkg -i $(sed 's/^[0-9a-f]*  //' /tmp/debs/SHA256SUMS) || true) \
 && for i in 1 2 3; do apt-get -f install -y && break; sleep 5; done \
 && for i in 1 2 3; do apt-get install -y php5-pgsql supervisor postgresql-client && break; sleep 5; done \
 && test "$(dpkg-query -W -f='${Version} ${Status}\n' zabbix-server-pgsql zabbix-agent zabbix-frontend-php \
            | grep -c '^1:2.4.3-1+trusty install ok installed$')" = 3 \
 && rm -rf /tmp/debs /var/lib/apt/lists/*

COPY zabbix_server.conf zabbix_agentd.conf /etc/zabbix/
COPY zabbix.conf.php /etc/zabbix/web/zabbix.conf.php
COPY apache-zabbix.conf /etc/apache2/conf-available/zabbix.conf
COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY run-daemon.sh entrypoint.sh /usr/local/bin/
COPY alertscripts/mp_alert.py /usr/lib/zabbix/alertscripts/mp_alert.py
RUN chmod 0755 /usr/local/bin/run-daemon.sh /usr/local/bin/entrypoint.sh \
               /usr/lib/zabbix/alertscripts/mp_alert.py \
 && chmod 0644 /etc/zabbix/web/zabbix.conf.php \
 && a2enconf zabbix \
 && mkdir -p /var/run/zabbix /var/log/zabbix /usr/lib/zabbix/alertscripts /usr/lib/zabbix/externalscripts \
 && chown zabbix:zabbix /var/run/zabbix /var/log/zabbix

EXPOSE 80 10050 10051
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
