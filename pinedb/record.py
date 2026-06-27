"""
record.py — Layer 2: Row serialization

Supports exactly two column types in V1:
  INT     — 4 bytes, signed int, struct format 'i'
  VARCHAR — 32 bytes, null-padded UTF-8 string
"""

import struct

class Schema:
    """
    Defines the layout of a record in a table.
    """
    def __init__(self, columns: list[tuple[str, str]]):
        """
        columns is a list of (col_name, col_type) e.g. [('id','INT'),('name','VARCHAR')]
        """
        self.columns = columns
        
        # Calculate row size
        self.row_size = 0
        for name, typ in columns:
            typ = typ.upper()
            if typ == 'INT':
                self.row_size += 4
            elif typ == 'VARCHAR':
                self.row_size += 32
            else:
                raise ValueError(f"Unsupported column type: {typ}")

    def col_index(self, name: str) -> int:
        for i, (col_name, _) in enumerate(self.columns):
            if col_name == name:
                return i
        raise ValueError(f"Column {name} not found in schema")

    def encode(self, values: list) -> bytes:
        if len(values) != len(self.columns):
            raise ValueError(f"Expected {len(self.columns)} values, got {len(values)}")
            
        data = b''
        for i, (name, typ) in enumerate(self.columns):
            val = values[i]
            typ = typ.upper()
            if typ == 'INT':
                data += struct.pack('>i', int(val))
            elif typ == 'VARCHAR':
                val_str = str(val).encode('utf-8')[:32]
                data += val_str.ljust(32, b'\x00')
        return data

    def decode(self, data: bytes) -> list:
        if len(data) != self.row_size:
            raise ValueError(f"Expected {self.row_size} bytes, got {len(data)}")
            
        values = []
        offset = 0
        for name, typ in self.columns:
            typ = typ.upper()
            if typ == 'INT':
                val = struct.unpack('>i', data[offset:offset+4])[0]
                values.append(val)
                offset += 4
            elif typ == 'VARCHAR':
                val = data[offset:offset+32].rstrip(b'\x00').decode('utf-8')
                values.append(val)
                offset += 32
        return values
