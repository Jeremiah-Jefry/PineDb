"""
parser.py — Layer 6: SQL Parser

Tokenizer and recursive descent parser for the PineDB SQL subset.
"""

import re
from dataclasses import dataclass

class ParseError(Exception):
    pass

# --- AST Nodes ---

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
    where_col: str | None
    where_val: int | str | None

@dataclass
class BeginTxn:
    pass

@dataclass
class CommitTxn:
    pass

# --- Tokenizer ---

TOK_KEYWORD = 'KEYWORD'
TOK_IDENTIFIER = 'IDENTIFIER'
TOK_INT_LITERAL = 'INT_LITERAL'
TOK_STRING_LITERAL = 'STRING_LITERAL'
TOK_LPAREN = 'LPAREN'
TOK_RPAREN = 'RPAREN'
TOK_COMMA = 'COMMA'
TOK_EQUALS = 'EQUALS'
TOK_STAR = 'STAR'
TOK_SEMICOLON = 'SEMICOLON'
TOK_EOF = 'EOF'

KEYWORDS = {
    'SELECT', 'FROM', 'WHERE', 'INSERT', 'INTO', 'VALUES',
    'CREATE', 'TABLE', 'BEGIN', 'COMMIT', 'INT', 'VARCHAR'
}

TOKEN_REGEX = re.compile(
    r'(?P<STRING_LITERAL>\'[^\']*\')|'
    r'(?P<INT_LITERAL>-?\d+)|'
    r'(?P<IDENTIFIER>[a-zA-Z_][a-zA-Z0-9_]*)|'
    r'(?P<LPAREN>\()|'
    r'(?P<RPAREN>\))|'
    r'(?P<COMMA>,)|'
    r'(?P<EQUALS>=)|'
    r'(?P<STAR>\*)|'
    r'(?P<SEMICOLON>;)|'
    r'(?P<WS>\s+)'
)

@dataclass
class Token:
    type: str
    value: str

class Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.tokens = []
        self._tokenize()

    def _tokenize(self):
        while self.pos < len(self.text):
            match = TOKEN_REGEX.match(self.text, self.pos)
            if not match:
                raise ParseError(f"Unexpected character at position {self.pos}: {self.text[self.pos]}")
            
            kind = match.lastgroup
            value = match.group(kind)
            self.pos = match.end()
            
            if kind == 'WS':
                continue
                
            if kind == 'IDENTIFIER' and value.upper() in KEYWORDS:
                kind = TOK_KEYWORD
                value = value.upper()
                
            if kind == 'STRING_LITERAL':
                value = value[1:-1] # strip quotes
                
            if kind == 'INT_LITERAL':
                value = int(value)
                
            self.tokens.append(Token(kind, value))
            
        self.tokens.append(Token(TOK_EOF, ''))

# --- Parser ---

class Parser:
    def __init__(self, text: str):
        self.tokenizer = Tokenizer(text)
        self.tokens = self.tokenizer.tokens
        self.pos = 0

    def current(self) -> Token:
        return self.tokens[self.pos]

    def consume(self, expected_type=None, expected_value=None) -> Token:
        tok = self.current()
        if expected_type and tok.type != expected_type:
            raise ParseError(f"Expected token type {expected_type}, got {tok.type}")
        if expected_value and tok.value != expected_value:
            raise ParseError(f"Expected '{expected_value}', got '{tok.value}'")
        self.pos += 1
        return tok

    def peek(self, offset=1) -> Token:
        if self.pos + offset < len(self.tokens):
            return self.tokens[self.pos + offset]
        return self.tokens[-1]

    def parse(self):
        tok = self.current()
        if tok.type == TOK_KEYWORD:
            if tok.value == 'CREATE':
                stmt = self.parse_create_table()
            elif tok.value == 'INSERT':
                stmt = self.parse_insert_into()
            elif tok.value == 'SELECT':
                stmt = self.parse_select()
            elif tok.value == 'BEGIN':
                stmt = self.parse_begin()
            elif tok.value == 'COMMIT':
                stmt = self.parse_commit()
            else:
                raise ParseError(f"Unexpected keyword: {tok.value}")
        else:
            raise ParseError(f"Expected statement, got {tok.type}")
            
        if self.current().type == TOK_SEMICOLON:
            self.consume(TOK_SEMICOLON)
            
        if self.current().type != TOK_EOF:
            raise ParseError("Unexpected tokens after statement")
            
        return stmt

    def parse_create_table(self):
        self.consume(TOK_KEYWORD, 'CREATE')
        self.consume(TOK_KEYWORD, 'TABLE')
        table_name = self.consume(TOK_IDENTIFIER).value
        self.consume(TOK_LPAREN)
        
        columns = []
        while True:
            col_name = self.consume(TOK_IDENTIFIER).value
            col_type_tok = self.consume(TOK_KEYWORD)
            if col_type_tok.value not in ('INT', 'VARCHAR'):
                raise ParseError(f"Expected INT or VARCHAR, got {col_type_tok.value}")
            columns.append((col_name, col_type_tok.value))
            
            if self.current().type == TOK_COMMA:
                self.consume(TOK_COMMA)
            else:
                break
                
        self.consume(TOK_RPAREN)
        return CreateTable(table_name, columns)

    def parse_insert_into(self):
        self.consume(TOK_KEYWORD, 'INSERT')
        self.consume(TOK_KEYWORD, 'INTO')
        table_name = self.consume(TOK_IDENTIFIER).value
        self.consume(TOK_KEYWORD, 'VALUES')
        self.consume(TOK_LPAREN)
        
        values = []
        while True:
            tok = self.current()
            if tok.type in (TOK_INT_LITERAL, TOK_STRING_LITERAL):
                values.append(tok.value)
                self.consume()
            else:
                raise ParseError(f"Expected value, got {tok.type}")
                
            if self.current().type == TOK_COMMA:
                self.consume(TOK_COMMA)
            else:
                break
                
        self.consume(TOK_RPAREN)
        return InsertInto(table_name, values)

    def parse_select(self):
        self.consume(TOK_KEYWORD, 'SELECT')
        self.consume(TOK_STAR)
        self.consume(TOK_KEYWORD, 'FROM')
        table_name = self.consume(TOK_IDENTIFIER).value
        
        where_col = None
        where_val = None
        
        if self.current().type == TOK_KEYWORD and self.current().value == 'WHERE':
            self.consume(TOK_KEYWORD, 'WHERE')
            where_col = self.consume(TOK_IDENTIFIER).value
            self.consume(TOK_EQUALS)
            
            tok = self.current()
            if tok.type in (TOK_INT_LITERAL, TOK_STRING_LITERAL):
                where_val = tok.value
                self.consume()
            else:
                raise ParseError(f"Expected value after '=', got {tok.type}")
                
        return Select(table_name, where_col, where_val)

    def parse_begin(self):
        self.consume(TOK_KEYWORD, 'BEGIN')
        return BeginTxn()

    def parse_commit(self):
        self.consume(TOK_KEYWORD, 'COMMIT')
        return CommitTxn()
