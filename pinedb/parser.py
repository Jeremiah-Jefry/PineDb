from dataclasses import dataclass
import re

class ParseError(Exception): pass

class Token:
    def __init__(self, type: str, value: str | int | None = None):
        self.type = type
        self.value = value
    def __repr__(self):
        return f"Token({self.type}, {self.value})"

KEYWORDS = {
    'SELECT': 'KW_SELECT',
    'FROM': 'KW_FROM',
    'WHERE': 'KW_WHERE',
    'INSERT': 'KW_INSERT',
    'INTO': 'KW_INTO',
    'VALUES': 'KW_VALUES',
    'CREATE': 'KW_CREATE',
    'TABLE': 'KW_TABLE',
    'BEGIN': 'KW_BEGIN',
    'COMMIT': 'KW_COMMIT',
    'INT': 'KW_INT',
    'VARCHAR': 'KW_VARCHAR'
}

class Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def tokenize(self) -> list[Token]:
        tokens = []
        while self.pos < len(self.text):
            char = self.text[self.pos]

            if char.isspace():
                self.pos += 1
                continue

            if char == '(':
                tokens.append(Token('LPAREN'))
                self.pos += 1
                continue
            if char == ')':
                tokens.append(Token('RPAREN'))
                self.pos += 1
                continue
            if char == ',':
                tokens.append(Token('COMMA'))
                self.pos += 1
                continue
            if char == '=':
                tokens.append(Token('EQUALS'))
                self.pos += 1
                continue
            if char == '*':
                tokens.append(Token('STAR'))
                self.pos += 1
                continue
            if char == ';':
                tokens.append(Token('SEMICOLON'))
                self.pos += 1
                continue

            if char == "'":
                self.pos += 1
                start = self.pos
                while self.pos < len(self.text) and self.text[self.pos] != "'":
                    self.pos += 1
                if self.pos >= len(self.text):
                    raise ParseError("Unterminated string literal")
                val = self.text[start:self.pos]
                tokens.append(Token('STR_LIT', val))
                self.pos += 1
                continue

            if char.isdigit() or (char == '-' and self.pos + 1 < len(self.text) and self.text[self.pos+1].isdigit()):
                start = self.pos
                self.pos += 1
                while self.pos < len(self.text) and self.text[self.pos].isdigit():
                    self.pos += 1
                val = int(self.text[start:self.pos])
                tokens.append(Token('INT_LIT', val))
                continue

            if char.isalpha() or char == '_':
                start = self.pos
                while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == '_'):
                    self.pos += 1
                val = self.text[start:self.pos]
                val_upper = val.upper()
                if val_upper in KEYWORDS:
                    tokens.append(Token(KEYWORDS[val_upper]))
                else:
                    tokens.append(Token('IDENT', val))
                continue

            raise ParseError(f"Unexpected character: {char}")

        tokens.append(Token('EOF'))
        return tokens

@dataclass
class CreateTable:
    table_name: str
    columns: list[tuple[str, str]]

@dataclass
class InsertInto:
    table_name: str
    values: list

@dataclass
class Select:
    table_name: str
    where_col: str | None = None
    where_val: object = None

@dataclass
class BeginTxn: pass

@dataclass
class CommitTxn: pass

class Parser:
    def __init__(self, text: str):
        self.tokens = Tokenizer(text).tokenize()
        self.pos = 0

    def peek(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token('EOF')

    def consume(self, expected_type: str) -> Token:
        tok = self.peek()
        if tok.type == expected_type:
            self.pos += 1
            return tok
        raise ParseError(f"Expected {expected_type}, got {tok.type}")

    def parse(self) -> CreateTable | InsertInto | Select | BeginTxn | CommitTxn:
        tok = self.peek()
        if tok.type == 'KW_CREATE':
            return self.parse_create_table()
        elif tok.type == 'KW_INSERT':
            return self.parse_insert_into()
        elif tok.type == 'KW_SELECT':
            return self.parse_select()
        elif tok.type == 'KW_BEGIN':
            self.consume('KW_BEGIN')
            if self.peek().type == 'SEMICOLON':
                self.consume('SEMICOLON')
            return BeginTxn()
        elif tok.type == 'KW_COMMIT':
            self.consume('KW_COMMIT')
            if self.peek().type == 'SEMICOLON':
                self.consume('SEMICOLON')
            return CommitTxn()
        else:
            raise ParseError(f"Unexpected statement starting with {tok.type}")

    def parse_create_table(self) -> CreateTable:
        self.consume('KW_CREATE')
        self.consume('KW_TABLE')
        table_name = self.consume('IDENT').value
        self.consume('LPAREN')

        columns = []
        while True:
            col_name = self.consume('IDENT').value
            type_tok = self.peek()
            if type_tok.type in ('KW_INT', 'KW_VARCHAR'):
                col_type = 'INT' if type_tok.type == 'KW_INT' else 'VARCHAR'
                self.consume(type_tok.type)
            else:
                raise ParseError(f"Expected INT or VARCHAR, got {type_tok.type}")
            columns.append((str(col_name), col_type))

            if self.peek().type == 'COMMA':
                self.consume('COMMA')
            else:
                break

        self.consume('RPAREN')
        if self.peek().type == 'SEMICOLON':
            self.consume('SEMICOLON')
        return CreateTable(str(table_name), columns)

    def parse_insert_into(self) -> InsertInto:
        self.consume('KW_INSERT')
        self.consume('KW_INTO')
        table_name = self.consume('IDENT').value
        self.consume('KW_VALUES')
        self.consume('LPAREN')

        values = []
        while True:
            tok = self.peek()
            if tok.type == 'INT_LIT':
                values.append(self.consume('INT_LIT').value)
            elif tok.type == 'STR_LIT':
                values.append(self.consume('STR_LIT').value)
            else:
                raise ParseError(f"Expected INT_LIT or STR_LIT, got {tok.type}")

            if self.peek().type == 'COMMA':
                self.consume('COMMA')
            else:
                break

        self.consume('RPAREN')
        if self.peek().type == 'SEMICOLON':
            self.consume('SEMICOLON')
        return InsertInto(str(table_name), values)

    def parse_select(self) -> Select:
        self.consume('KW_SELECT')
        self.consume('STAR')
        self.consume('KW_FROM')
        table_name = self.consume('IDENT').value

        where_col = None
        where_val = None
        if self.peek().type == 'KW_WHERE':
            self.consume('KW_WHERE')
            where_col = self.consume('IDENT').value
            self.consume('EQUALS')
            val_tok = self.peek()
            if val_tok.type == 'INT_LIT':
                where_val = self.consume('INT_LIT').value
            elif val_tok.type == 'STR_LIT':
                where_val = self.consume('STR_LIT').value
            else:
                raise ParseError(f"Expected INT_LIT or STR_LIT in WHERE clause, got {val_tok.type}")

        if self.peek().type == 'SEMICOLON':
            self.consume('SEMICOLON')
        return Select(str(table_name), where_col if where_col else None, where_val)
