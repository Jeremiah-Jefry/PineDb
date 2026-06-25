import struct

RECORD_FORMAT = "<I32s"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)  # 36 bytes

def encode(id_, name):
    return struct.pack(RECORD_FORMAT, id_, name.encode().ljust(32, b'\x00'))

def decode(data):
    id_, name = struct.unpack(RECORD_FORMAT, data)
    return id_, name.rstrip(b'\x00').decode()