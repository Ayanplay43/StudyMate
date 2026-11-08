from server import *
import ssl

if not DB_PATH.is_file():
    raise SystemExit("Database missing")

migrate_database()

httpd = ThreadingHTTPServer((HOST, PORT), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("certs/localhost.pem", "certs/localhost-key.pem")
httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

print("StudyMate: https://localhost:8000")
httpd.serve_forever()
