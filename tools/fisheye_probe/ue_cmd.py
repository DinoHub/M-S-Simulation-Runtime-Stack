"""Send one Unreal console command to AirSim over its msgpack-rpc port.

    python3 ue_cmd.py "startfpschart"      [host] [port]

AirSim's RPC server is msgpack-rpc: a request is [0, msgid, method, params] and
the reply [1, msgid, error, result]. simRunConsoleCommand returns a bool.
"""
import socket
import sys

import msgpack

cmd = sys.argv[1]
host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
port = int(sys.argv[3]) if len(sys.argv) > 3 else 41451

with socket.create_connection((host, port), timeout=10) as s:
    s.sendall(msgpack.packb([0, 1, "simRunConsoleCommand", [cmd]]))
    unpacker = msgpack.Unpacker(raw=False)
    while True:
        data = s.recv(65536)
        if not data:
            sys.exit("connection closed without a reply")
        unpacker.feed(data)
        for msg in unpacker:
            _, _, err, result = msg
            if err:
                sys.exit(f"error: {err}")
            print(result)
            sys.exit(0)
