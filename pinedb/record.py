import struct

class Schema:
    def __init__(self, columns: list[tuple[str, str]]):
        self.columns = columns
        self._col_names = [col[0] for col in columns]
        self._col_types = [col[1] for col in columns]

    @property
    def row_size(self) -> int:
        size = 0
        for name, col_type in self.columns:
            if col_type == 'INT':
                size += 4
            elif col_type == 'VARCHAR':
                size += 32
            else:
                raise ValueError(f"Unsupported column type {col_type}")
        return size

    def col_index(self, name: str) -> int:
        try:
            return self._col_names.index(name)
        except ValueError:
            raise KeyError(f"Column {name} not found")

    def encode(self, values: list) -> bytes:
        if len(values) != len(self.columns):
            raise ValueError("Number of values does not match schema")

        encoded_bytes = bytearray()
        for i, val in enumerate(values):
            col_type = self._col_types[i]
            if col_type == 'INT':
                encoded_bytes.extend(struct.pack('>i', int(val)))
            elif col_type == 'VARCHAR':
                str_val = str(val).encode('utf-8')[:32]
                encoded_bytes.extend(str_val.ljust(32, b'\x00'))

        res = bytes(encoded_bytes)
        assert len(res) == self.row_size
        return res

    def decode(self, data: bytes) -> list:
        if len(data) != self.row_size:
            raise ValueError(f"Data length {len(data)} does not match row size {self.row_size}")

        values = []
        offset = 0
        for col_type in self._col_types:
            if col_type == 'INT':
                val = struct.unpack('>i', data[offset:offset+4])[0]
                values.append(val)
                offset += 4
            elif col_type == 'VARCHAR':
                val_bytes = data[offset:offset+32]
                val = val_bytes.rstrip(b'\x00').decode('utf-8')
                values.append(val)
                offset += 32
        return values

    def col_names(self) -> list[str]:
        return self._col_names
