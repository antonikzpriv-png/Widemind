"""Real loopback TLS only. No external providers, accounts, or credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

from widemind.tools.http import HTTPS, TransportError, _BoundedHTTPSHandler, _NoRedirect


@unittest.skipUnless(shutil.which('openssl'), 'openssl required for an ephemeral local TLS certificate')
class TransportTLSTests(unittest.TestCase):
    def test_verified_tls_works_and_an_untrusted_certificate_never_gets_http_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                            '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1',
                            '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            requests=[]
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append(self.path)
                    body=b'{"verified_local_tls":true}'
                    self.send_response(200)
                    self.send_header('Content-Length',str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self,*args): pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root/'cert.pem',root/'key.pem')
            server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            def client(context):
                transport=HTTPS()
                handler=_BoundedHTTPSHandler(time.monotonic)
                handler._context=context
                # Explicit loopback fixture; production retains inherited proxies.
                transport.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect(),handler)
                return transport
            try:
                url=f'https://localhost:{server.server_port}/test'
                trusted=client(ssl.create_default_context(cafile=str(root/'cert.pem')))
                response=trusted.request('GET',url,timeout=5)
                self.assertEqual(response.status,200)
                self.assertTrue(response.json()['verified_local_tls'])
                untrusted=client(ssl.create_default_context())
                with self.assertRaises(TransportError):
                    untrusted.request('GET',url,timeout=5)
                self.assertEqual(requests,['/test'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__=='__main__':
    unittest.main()
