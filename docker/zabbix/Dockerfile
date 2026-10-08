FROM ubuntu:14.04

ENV DEBIAN_FRONTEND=noninteractive
ENV ZABBIX_VERSION=2.4.3-1+trusty
ENV ZABBIX_POOL=http://repo.zabbix.com/zabbix/2.4/ubuntu/pool/main/z/zabbix

# The repository index only lists the newest 2.4.x, so the 2.4.3 packages
# are fetched by exact file name and their dependencies resolved with
# "apt-get -f install".  dbconfig-common is installed first and told not to
# touch a database during the build (the server package's dbconfig answer is
# also written to its dbconfig-common file, which dbconfig-common reads back
# over the debconf value); the entrypoint loads the schema.
RUN for i in 1 2 3; do apt-get update && break; sleep 5; done \
 && for i in 1 2 3; do apt-get install -y --no-install-recommends wget ca-certificates dbconfig-common && break; sleep 5; done \
 && echo 'zabbix-server-pgsql zabbix-server-pgsql/dbconfig-install boolean false' | debconf-set-selections \
 && echo 'zabbix-frontend-php zabbix-frontend-php/dbconfig-install boolean false' | debconf-set-selections \
 && mkdir -p /etc/dbconfig-common \
 && printf "dbc_install='false'\ndbc_dbtype='pgsql'\n" > /etc/dbconfig-common/zabbix-server-pgsql.conf \
 && mkdir /tmp/debs && cd /tmp/debs \
 && for pkg in zabbix-server-pgsql_${ZABBIX_VERSION}_amd64 \
               zabbix-agent_${ZABBIX_VERSION}_amd64 \
               zabbix-frontend-php_${ZABBIX_VERSION}_all; do \
      wget -q --tries=3 --waitretry=5 "${ZABBIX_POOL}/${pkg}.deb" || exit 1; \
    done \
 && (dpkg -i /tmp/debs/*.deb || true) \
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
RUN chmod 0755 /usr/local/bin/run-daemon.sh /usr/local/bin/entrypoint.sh \
 && chmod 0644 /etc/zabbix/web/zabbix.conf.php \
 && a2enconf zabbix \
 && mkdir -p /var/run/zabbix /var/log/zabbix /usr/lib/zabbix/alertscripts /usr/lib/zabbix/externalscripts \
 && chown zabbix:zabbix /var/run/zabbix /var/log/zabbix

EXPOSE 80 10050 10051
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
