# The image used for every dynamically created "bench unit" container.
# Pin a specific tag in production; `latest` is used here for simplicity.
FROM frappe/bench:latest

USER root

# The bundled MariaDB client defaults to requiring TLS, but our mariadb
# service has none configured — without this, every `bench new-site`/
# `backup`/`restore` fails with "SSL is required, but the server does not
# support it". Also raise the client's max_allowed_packet to match the
# server's (see docker-compose.yml) — otherwise `bench restore` can still
# fail partway through a real-world dump with "Server has gone away" on a
# large attachment blob even though the server itself would accept it.
RUN printf '[client]\nssl = false\nmax_allowed_packet = 512M\n' > /etc/mysql/conf.d/disable-ssl.cnf

# The base image's own /etc/mysql/conf.d/mysqldump.cnf pins
# [mysqldump] max_allowed_packet=16M specifically — conf.d files are read
# in alphabetical order and the more specific [mysqldump]/[mariadb-dump]
# group wins over our [client] setting above regardless of which file
# it's in, so a row bigger than 16M (e.g. a large exported report logged
# in `tabAccess Log`) breaks `bench backup` with "Lost connection to
# server during query" even though both our [client] override and the
# server itself already allow up to 512M. Named to sort after
# "mysqldump.cnf" so this is the one actually left standing.
RUN printf '[mysqldump]\nmax_allowed_packet = 512M\n\n[mariadb-dump]\nmax_allowed_packet = 512M\n' \
    > /etc/mysql/conf.d/zzz-platform-overrides.cnf

# Not in the base image — `bench backup` needs it whenever a site has
# "Encrypt Backups" turned on in System Settings (frappe/utils/backups.py),
# otherwise it refuses with "Please install `gpg`" partway through.
RUN apt-get update && apt-get install -y --no-install-recommends gnupg && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 8000: Frappe web process (werkzeug/gunicorn). 9000: Frappe's socketio process.
EXPOSE 8000 9000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
