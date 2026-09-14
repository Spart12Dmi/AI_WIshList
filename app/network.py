"""Verified HTTPS using the operating system trust store, never verify=False."""

import os
import ssl

import truststore


def tls_context():
    # Explicit enterprise CA configuration remains supported.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
