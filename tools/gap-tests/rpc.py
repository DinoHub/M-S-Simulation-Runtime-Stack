"""Minimal msgpack-rpc client for AirSim (one persistent connection)."""
import socket, itertools, msgpack

class Rpc:
    def __init__(self, host="127.0.0.1", port=41451, timeout=60):
        self.addr, self.timeout = (host, port), timeout
        self.ids = itertools.count(1)
        self._connect()

    def _connect(self):
        self.s = socket.create_connection(self.addr, timeout=self.timeout)
        self.u = msgpack.Unpacker(raw=False, max_buffer_size=1 << 31)

    def __call__(self, method, *params):
        mid = next(self.ids)
        self.s.sendall(msgpack.packb([0, mid, method, list(params)], use_bin_type=True))
        while True:
            d = self.s.recv(1 << 22)
            if not d:
                raise ConnectionError("rpc closed")
            self.u.feed(d)
            for m in self.u:
                if m[1] != mid:
                    continue
                if m[2]:
                    raise RuntimeError(f"{method}: {m[2]}")
                return m[3]
