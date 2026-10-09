"""Contexto TLS con un almacén de autoridades que exista de verdad.

El binario de Linux se compila en Ubuntu 22.04, y el OpenSSL que va dentro
busca las autoridades de certificación en las rutas de Debian/Ubuntu
(``/usr/lib/ssl/cert.pem``, ``/usr/lib/ssl/certs``). En Arch, Fedora y otras
distros esas rutas **no existen**, así que todas las peticiones HTTPS fallaban
con::

    CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate

Y el fallo quedaba medio tapado, que es lo peor: el listado de compilaciones caía
al caché de disco (parecía que había red) y el aviso de actualización decía "ya
tienes la última versión" cuando en realidad no había podido preguntar. Se
detectó en Arch justo así, al no ofrecer la actualización a la 1.4.0.

Aquí se busca un almacén que exista y se le pasa explícitamente a ``urlopen``.
"""

import ssl
from pathlib import Path

# Rutas habituales, en orden. La primera suele valer para Debian, Ubuntu, Mint y
# Arch; las demás cubren Fedora/RHEL, openSUSE, Alpine y los BSD.
CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ssl/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/usr/local/share/certs/ca-root-nss.crt",
)

# El resultado no cambia mientras corre la aplicación: se mira una vez.
_ca_file = None


def ca_file() -> str | None:
    """Primer almacén de CAs que exista en este sistema, o ``None``.

    ``None`` no significa "sin verificar": en ese caso se usa el contexto por
    defecto de Python, que es lo que había antes de este arreglo.
    """
    global _ca_file
    if _ca_file is None:
        for candidate in CA_CANDIDATES:
            if Path(candidate).is_file():
                _ca_file = candidate
                break
        else:
            _ca_file = ""
    return _ca_file or None


def ssl_context() -> ssl.SSLContext:
    """Contexto TLS para ``urllib``, con un almacén de CAs que exista.

    Se crea uno por petición (no se comparte el objeto entre hilos): localizar
    el fichero sí se recuerda, que es lo único que se repetiría.
    """
    path = ca_file()
    if path:
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()
