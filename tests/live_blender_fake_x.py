"""A stand-in for Xephyr in Dream's tests (DREAM-109): it opens no window anywhere.

The launcher runs it exactly where the nested X server runs, in the nested server's own
bubblewrap, with that server's arguments. It listens where the server would (its
/tmp/.X11-unix, which is the session's private folder), accepts and drops connections,
and writes down what it saw next to its socket.
"""
import json
import os
import socket
import sys

args = sys.argv[1:]
number = next(a for a in args if a.startswith(":"))[1:]
server = socket.socket(socket.AF_UNIX)
server.bind(f"/tmp/.X11-unix/X{number}")
server.listen(16)


def reaches(address: str) -> bool:
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(address)
        return True
    except OSError:
        return False
    finally:
        probe.close()


host = os.environ.get("DISPLAY", ":").split(":")[1].split(".")[0]
record = {
    "argv": args,
    "env": sorted(os.environ),
    "display": os.environ.get("DISPLAY"),
    "socket_folder": sorted(os.listdir("/tmp/.X11-unix")),
    "cookie": open("/tmp/xauth", "rb").read().hex(),
    "host_abstract": reaches("\0/tmp/.X11-unix/X" + host),  # connect and drop: no X request, no window
    "host_path_visible": os.path.exists("/tmp/.X11-unix/X" + host),
}
with open(f"/tmp/.X11-unix/fake-X{number}.json", "w") as out:
    json.dump(record, out)
while True:
    connection, _ = server.accept()
    connection.close()
