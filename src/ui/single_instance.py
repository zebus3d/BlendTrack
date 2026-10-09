"""Una sola instancia de Blender Manager por usuario y configuración.

Abrir la aplicación dos veces no aporta nada: se solapan dos ventanas, dos
iconos de bandeja y dos listados descargándose a la vez. La primera instancia
escucha en un socket local con nombre; si el usuario vuelve a abrirla (doble
clic en el AppImage, el lanzador, el menú...), la nueva le pide por ese socket
que **salga al frente** y se va sin crear su propia ventana. Funciona igual en
Linux, Windows y macOS —``QLocalServer`` usa sockets UNIX o *named pipes* según
el sistema— y sirve también cuando la que ya corría estaba escondida en la
bandeja, que es justo el caso en el que el usuario cree que no hay ninguna
abierta.

Esto vive en ``ui/`` porque usa Qt; ``services/`` sigue sin depender de la
interfaz.
"""

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from services import settings as settings_service


def socket_name() -> str:
    """Nombre del socket, único por usuario y carpeta de configuración.

    Se deriva de la carpeta de config para que un modo **portable** (que guarda
    sus ajustes en otro sitio) no choque con la app instalada, y para que dos
    usuarios de la misma máquina no se pisen. El hash es solo para que el
    nombre quede corto y sin caracteres raros.
    """
    base = f"{Path.home()}|{settings_service.config_dir()}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"blendermanager-{digest}"


class SingleInstance(QObject):
    """Coordina la instancia actual con cualquier otra que ya esté corriendo.

    El uso es siempre el mismo::

        single = SingleInstance(app)
        if single.notify_existing():
            return 0          # ya había una; se le pidió que salga al frente
        single.listen(callback)   # callback = enseñar la ventana propia

    Si ``listen`` falla (otro proceso se adelantó, un socket huérfano de un
    cierre brusco...), no es fatal: la aplicación sigue arrancando sin la
    protección, que es mejor que no arrancar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = socket_name()
        self._server: QLocalServer | None = None
        self._on_activate = None

    def notify_existing(self) -> bool:
        """Avisa a otra instancia, si la hay, y dice si había alguna.

        Devuelve ``True`` cuando pudo conectarse al socket: en ese caso el
        proceso que llama debe terminar sin abrir ventana.
        """
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(300):
            return False
        # El mensaje en sí da igual: la sola conexión ya significa "oye, sal".
        # Aun así se escribe algo para que el otro lado tenga de dónde leer y se
        # cierra el ciclo con una espera corta.
        socket.write(b"show")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True

    def listen(self, on_activate) -> bool:
        """Se pone a escuchar y llama a ``on_activate`` en cada nueva apertura.

        ``on_activate`` no recibe argumentos: la ventana que hay que enseñar la
        conoce quien llama. Como se escucha **antes** de crear la ventana (para
        no dejar un hueco en el que una segunda apertura se cuele), quien llama
        pasa una función que mira si la ventana ya existe y, si todavía no,
        no hace nada: la ventana está a punto de aparecer de todos modos (ver
        ``main.run_ui``). Devuelve ``False`` si no se pudo escuchar.
        """
        self._on_activate = on_activate
        # Un cierre brusco (kill, cuelgue) deja el socket huérfano y ``listen``
        # fallaría para siempre con "address in use": se borra antes de escuchar.
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        # Solo el usuario que abrió la app puede conectarse. En Linux y macOS el
        # socket es un fichero, y sin esta opción nace con los permisos que
        # dicte la ``umask``: con una ``umask`` de grupo escribible, otro
        # usuario del grupo podría conectarse y sacar nuestra ventana al frente
        # (o, si algún día el protocolo hace algo más, pedirle cosas). En
        # Windows la opción restringe igual el *named pipe*.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def close(self) -> None:
        """Deja de escuchar y borra el socket (para tests y cierres limpios)."""
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._name)
            self._server = None

    # ------------------------------------------------------------------ privado
    def _on_connection(self) -> None:
        """Atiende las conexiones pendientes y trae la ventana al frente."""
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.readAll()
            connection.disconnectFromServer()
        if self._on_activate is not None:
            self._on_activate()
