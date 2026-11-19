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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 8000: Frappe web process (werkzeug/gunicorn). 9000: Frappe's socketio process.
EXPOSE 8000 9000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
