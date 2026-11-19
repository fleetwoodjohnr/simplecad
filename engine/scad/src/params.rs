//! Expressions: which parameters one names (`simplecad/core/params.py` `dependencies`).
//!
//! The Python app answers by running Python's own parser over the text, after
//! rewriting unit suffixes, and collecting every `Name`. So any string input that
//! parses as an expression names parameters, mode words included (`"top"`,
//! `"sans-serif"`), and that reaches every cache signature. This module gives the
//! same answers: the same unit rewriting, then Python's expression grammar.
//! `tests/data/dependencies.json` (every string input of the Desktop projects,
//! plus edge cases) and `tests/data/dependencies_fuzz.json` hold Python's answers.
//!
//! ponytail: identifiers are not NFKC-normalised and non-ASCII digits are not
//! treated as digits, as Python does; f-strings with nested quotes in a field
//! are not parsed. None occurs in a project yet.

use std::collections::BTreeSet;

/// Names an expression may use that are not parameters (`FUNCTIONS`, `CONSTANTS`).
const BUILTINS: &[&str] = &[
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "abs", "min", "max", "floor",
    "ceil", "round", "hypot", "log", "exp", "pi", "e", "tau",
];

/// `units.py`'s length and angle unit names, for `known_unit`.
const UNITS: &[&str] = &[
    "mm",
    "millimeter",
    "millimetre",
    "cm",
    "centimeter",
    "centimetre",
    "m",
    "meter",
    "metre",
    "in",
    "inch",
    "\"",
    "ft",
    "foot",
    "thou",
    "mil",
    "deg",
    "degree",
    "degrees",
    "°",
    "rad",
    "radian",
    "radians",
];

/// The parameter names `expression` uses; empty for text that is not an expression.
pub fn dependencies(expression: &str) -> BTreeSet<String> {
    if !expression.chars().any(|c| c.is_alphabetic() || c == '_') {
        return BTreeSet::new();
    }
    let mut names = BTreeSet::new();
    let text = tokenize_units(expression);
    let Some(tokens) = lex(&text) else {
        return names;
    };
    let mut parser = Parser {
        tokens,
        at: 0,
        names: Vec::new(),
    };
    if parser.expressions().is_some() && parser.at == parser.tokens.len() {
        names.extend(
            parser
                .names
                .into_iter()
                .filter(|n| !BUILTINS.contains(&n.as_str())),
        );
    }
    names
}

fn known_unit(unit: &str) -> bool {
    !unit.is_empty() && (UNITS.contains(&unit) || UNITS.contains(&unit.to_lowercase().as_str()))
}

/// `_tokenize_units`: a number followed by a known unit becomes `(number * factor)`.
/// The factor is irrelevant to which names an expression uses.
fn tokenize_units(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let digit = |i: usize| chars.get(i).is_some_and(|c| c.is_ascii_digit());
    let (n, mut i, mut out) = (chars.len(), 0, String::new());
    while i < n {
        if digit(i) || (chars[i] == '.' && digit(i + 1)) {
            let start = i;
            while i < n && (chars[i].is_ascii_digit() || chars[i] == '.') {
                i += 1;
            }
            if i < n && (chars[i] == 'e' || chars[i] == 'E') {
                let mut probe = i + 1;
                if probe < n && (chars[probe] == '+' || chars[probe] == '-') {
                    probe += 1;
                }
                if digit(probe) {
                    i = probe;
                    while digit(i) {
                        i += 1;
                    }
                }
            }
            let number: String = chars[start..i].iter().collect();
            let mut gap = i;
            while gap < n && chars[gap] == ' ' {
                gap += 1;
            }
            let unit_start = gap;
            while gap < n && (chars[gap].is_alphabetic() || chars[gap] == '°' || chars[gap] == '"')
            {
                gap += 1;
            }
            let unit: String = chars[unit_start..gap].iter().collect();
            if known_unit(&unit) {
                out.push_str(&format!("({number} * 1.0)"));
                i = gap;
            } else {
                out.push_str(&number);
            }
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

#[derive(Clone, Debug, PartialEq)]
enum Token {
    Name(String),
    Keyword(&'static str),
    Number,
    /// A string literal: no names. Adjacent strings join; a number does not.
    Literal,
    /// An f-string: the names its replacement fields use.
    FString(Vec<String>),
    Op(&'static str),
}

const KEYWORDS: &[&str] = &[
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import",
    "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
    "with", "yield",
];

/// Longest first, so `**=` is not read as `**` then `=`.
const OPERATORS: &[&str] = &[
    "**=", "//=", ">>=", "<<=", "...", "->", ":=", "**", "//", "<<", ">>", "<=", ">=", "==", "!=",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=", "+", "-", "*", "/", "%", "@", "&", "|",
    "^", "~", "<", ">", "(", ")", "[", "]", "{", "}", ",", ":", ".", ";", "=",
];

fn ident_start(c: char) -> bool {
    c == '_' || c.is_alphabetic()
}

fn ident_continue(c: char) -> bool {
    c == '_' || c.is_alphabetic() || c.is_ascii_digit()
}

/// Python's tokenizer, as far as an `eval` of one expression needs it; `None` is a
/// `SyntaxError`.
fn lex(text: &str) -> Option<Vec<Token>> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut tokens = Vec::new();
    let mut depth = 0usize;
    let mut i = 0;
    while i < n {
        let c = chars[i];
        match c {
            // Blank lines may come first; an indented first line is an indent, which eval mode refuses.
            ' ' | '\t' | '\x0c' if tokens.is_empty() => {
                let mut j = i;
                while j < n && matches!(chars[j], ' ' | '\t' | '\x0c') {
                    j += 1;
                }
                if j < n && !matches!(chars[j], '\n' | '\r' | '#') {
                    return None;
                }
                i = j;
            }
            ' ' | '\t' | '\x0c' => i += 1,
            '#' => {
                while i < n && chars[i] != '\n' {
                    i += 1;
                }
            }
            '\n' | '\r' => {
                // Inside brackets a line break is whitespace; outside, only trailing ones are allowed.
                if depth == 0 && !tokens.is_empty() && chars[i..].iter().any(|c| !c.is_whitespace())
                {
                    return None;
                }
                i += 1;
            }
            '\\' => {
                // A line continuation joins lines; anything else after a backslash is an error.
                match chars.get(i + 1) {
                    Some('\n') => i += 2,
                    Some('\r') if chars.get(i + 2) == Some(&'\n') => i += 3,
                    _ => return None,
                }
            }
            _ if c.is_ascii_digit()
                || (c == '.' && chars.get(i + 1).is_some_and(|d| d.is_ascii_digit())) =>
            {
                i = number(&chars, i)?;
                tokens.push(Token::Number);
            }
            _ if ident_start(c) => {
                let start = i;
                while i < n && ident_continue(chars[i]) {
                    i += 1;
                }
                let word: String = chars[start..i].iter().collect();
                if i < n && (chars[i] == '\'' || chars[i] == '"') && string_prefix(&word).is_some()
                {
                    let (end, token) = string(&chars, i, string_prefix(&word)?)?;
                    i = end;
                    tokens.push(token);
                } else if let Some(k) = KEYWORDS.iter().find(|k| **k == word) {
                    tokens.push(Token::Keyword(k));
                } else {
                    tokens.push(Token::Name(word));
                }
            }
            '\'' | '"' => {
                let (end, token) = string(&chars, i, Prefix::default())?;
                i = end;
                tokens.push(token);
            }
            _ => {
                let rest: String = chars[i..n.min(i + 3)].iter().collect();
                let op = OPERATORS.iter().find(|op| rest.starts_with(**op))?;
                match *op {
                    "(" | "[" | "{" => depth += 1,
                    ")" | "]" | "}" => depth = depth.checked_sub(1)?,
                    _ => {}
                }
                i += op.chars().count();
                tokens.push(Token::Op(op));
            }
        }
    }
    (depth == 0).then_some(tokens)
}

/// The end of the number literal starting at `i`, Python's rules: underscores
/// only between digits, no leading zeros on a non-zero integer, and no letter
/// straight after (`1.a`, `1e`).
fn number(chars: &[char], start: usize) -> Option<usize> {
    let n = chars.len();
    let mut i = start;
    let digits = |i: &mut usize, ok: &dyn Fn(char) -> bool| -> Option<usize> {
        let begin = *i;
        while *i < n
            && (ok(chars[*i])
                || (chars[*i] == '_' && *i > begin && *i + 1 < n && ok(chars[*i + 1])))
        {
            *i += 1;
        }
        (*i > begin).then_some(*i - begin)
    };
    let radix = |c: char| match c {
        'x' | 'X' => Some(16),
        'o' | 'O' => Some(8),
        'b' | 'B' => Some(2),
        _ => None,
    };
    if chars[i] == '0' && i + 1 < n && radix(chars[i + 1]).is_some() {
        let base = radix(chars[i + 1])?;
        i += 2;
        if i < n && chars[i] == '_' {
            i += 1;
        }
        digits(&mut i, &|c: char| c.is_digit(base))?;
    } else {
        let mut float = false;
        let int_start = i;
        if chars[i] != '.' {
            digits(&mut i, &|c: char| c.is_ascii_digit())?;
        }
        let integer: String = chars[int_start..i].iter().filter(|c| **c != '_').collect();
        if i < n && chars[i] == '.' {
            float = true;
            i += 1;
            if i < n && chars[i].is_ascii_digit() {
                digits(&mut i, &|c: char| c.is_ascii_digit())?;
            }
        }
        if i < n && (chars[i] == 'e' || chars[i] == 'E') {
            float = true;
            i += 1;
            if i < n && (chars[i] == '+' || chars[i] == '-') {
                i += 1;
            }
            digits(&mut i, &|c: char| c.is_ascii_digit())?;
        }
        if i < n && (chars[i] == 'j' || chars[i] == 'J') {
            float = true;
            i += 1;
        }
        if !float
            && integer.len() > 1
            && integer.starts_with('0')
            && integer.chars().any(|c| c != '0')
        {
            return None;
        }
    }
    if i < n && (ident_continue(chars[i]) || chars[i] == '.') && chars[i] != '_'
        || (i < n && chars[i] == '_')
    {
        // `1.a`, `1_`, `0b2`: Python's "invalid decimal literal". A dot after an
        // integer was taken above; one after a float (`1.5.`) is left to the parser.
        if !(chars[i] == '.' && chars[start..i].contains(&'.')) {
            return None;
        }
    }
    Some(i)
}

#[derive(Clone, Copy, Default)]
struct Prefix {
    raw: bool,
    bytes: bool,
    format: bool,
}

fn string_prefix(word: &str) -> Option<Prefix> {
    let lower = word.to_ascii_lowercase();
    let mut prefix = Prefix::default();
    match lower.as_str() {
        "r" => prefix.raw = true,
        "u" => {}
        "b" => prefix.bytes = true,
        "br" | "rb" => (prefix.raw, prefix.bytes) = (true, true),
        "f" | "t" => prefix.format = true,
        "fr" | "rf" | "tr" | "rt" => (prefix.raw, prefix.format) = (true, true),
        _ => return None,
    }
    Some(prefix)
}

/// A string literal from its opening quote at `i`; returns the end and the token.
fn string(chars: &[char], i: usize, prefix: Prefix) -> Option<(usize, Token)> {
    let n = chars.len();
    let quote = chars[i];
    let triple = i + 2 < n && chars[i + 1] == quote && chars[i + 2] == quote;
    let mut j = if triple { i + 3 } else { i + 1 };
    let mut body = String::new();
    loop {
        if j >= n {
            return None;
        }
        let c = chars[j];
        if c == quote && (!triple || (j + 2 < n && chars[j + 1] == quote && chars[j + 2] == quote))
        {
            j += if triple { 3 } else { 1 };
            break;
        }
        if (c == '\n' || c == '\r') && !triple {
            return None;
        }
        if prefix.bytes && !c.is_ascii() {
            return None;
        }
        if c == '\\' {
            let next = *chars.get(j + 1)?;
            if !prefix.raw && !escape_ok(chars, j + 1, prefix.bytes) {
                return None;
            }
            body.push(c);
            body.push(next);
            j += 2;
            continue;
        }
        body.push(c);
        j += 1;
    }
    if prefix.format {
        return Some((j, Token::FString(fstring_names(&body)?)));
    }
    Some((j, Token::Literal))
}

/// Whether the escape after a backslash is complete: `\x` needs 2 hex digits,
/// `\u` 4 and `\U` 8 (not in bytes), `\N{…}` a name. Unknown escapes are allowed.
fn escape_ok(chars: &[char], at: usize, bytes: bool) -> bool {
    let hex = |count: usize| {
        (1..=count).all(|k| chars.get(at + k).is_some_and(|c| c.is_ascii_hexdigit()))
    };
    match chars[at] {
        'x' => hex(2),
        'u' if !bytes => hex(4),
        'U' if !bytes => hex(8),
        'N' if !bytes => chars.get(at + 1) == Some(&'{') && chars[at + 1..].contains(&'}'),
        _ => true,
    }
}

/// The names an f-string's replacement fields use: `{expr!r:spec}`, `{{` an escape.
fn fstring_names(body: &str) -> Option<Vec<String>> {
    let chars: Vec<char> = body.chars().collect();
    let mut names = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        match chars[i] {
            '{' if chars.get(i + 1) == Some(&'{') => i += 2,
            '}' if chars.get(i + 1) == Some(&'}') => i += 2,
            '}' => return None,
            '{' => {
                let mut depth = 1;
                let mut j = i + 1;
                let mut end = None;
                while j < chars.len() {
                    match chars[j] {
                        '{' | '(' | '[' => depth += 1,
                        ')' | ']' => depth -= 1,
                        '}' => {
                            depth -= 1;
                            if depth == 0 {
                                break;
                            }
                        }
                        '!' | ':'
                            if depth == 1 && end.is_none() && chars.get(j + 1) != Some(&'=') =>
                        {
                            end = Some(j)
                        }
                        _ => {}
                    }
                    j += 1;
                }
                if j >= chars.len() {
                    return None;
                }
                let field: String = chars[i + 1..end.unwrap_or(j)].iter().collect();
                let field = field.trim_end_matches('=').trim();
                let tokens = lex(field)?;
                let mut parser = Parser {
                    tokens,
                    at: 0,
                    names: Vec::new(),
                };
                parser.expressions()?;
                if parser.at != parser.tokens.len() {
                    return None;
                }
                names.extend(parser.names);
                i = j + 1;
            }
            _ => i += 1,
        }
    }
    Some(names)
}

/// Python's expression grammar (eval mode), recording every `Name` it reads.
struct Parser {
    tokens: Vec<Token>,
    at: usize,
    names: Vec<String>,
}

type Parsed = Option<()>;

impl Parser {
    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.at)
    }

    fn peek_at(&self, offset: usize) -> Option<&Token> {
        self.tokens.get(self.at + offset)
    }

    fn op(&mut self, op: &str) -> bool {
        if matches!(self.peek(), Some(Token::Op(o)) if *o == op) {
            self.at += 1;
            true
        } else {
            false
        }
    }

    fn keyword(&mut self, word: &str) -> bool {
        if matches!(self.peek(), Some(Token::Keyword(k)) if *k == word) {
            self.at += 1;
            true
        } else {
            false
        }
    }

    fn is_op(&self, op: &str) -> bool {
        matches!(self.peek(), Some(Token::Op(o)) if *o == op)
    }

    fn is_keyword(&self, word: &str) -> bool {
        matches!(self.peek(), Some(Token::Keyword(k)) if *k == word)
    }

    /// Something that may begin an expression: keeps `a,` and `a,)` apart from `a, b`.
    fn starts_expression(&self) -> bool {
        match self.peek() {
            Some(Token::Name(_) | Token::Number | Token::Literal | Token::FString(_)) => true,
            Some(Token::Keyword(k)) => {
                ["None", "True", "False", "not", "lambda", "await"].contains(k)
            }
            Some(Token::Op(o)) => ["(", "[", "{", "-", "+", "~", "...", "*"].contains(o),
            None => false,
        }
    }

    /// `expressions`: what eval mode parses, a tuple without parentheses allowed.
    fn expressions(&mut self) -> Parsed {
        self.expression()?;
        while self.op(",") {
            if !self.starts_expression() {
                break;
            }
            self.star_or_expression()?;
        }
        Some(())
    }

    fn star_or_expression(&mut self) -> Parsed {
        if self.op("*") {
            return self.bitwise_or();
        }
        self.expression()
    }

    fn named_expression(&mut self) -> Parsed {
        if matches!(self.peek(), Some(Token::Name(_)))
            && matches!(self.peek_at(1), Some(Token::Op(":=")))
        {
            self.atom()?;
            self.at += 1;
            return self.expression();
        }
        self.expression()
    }

    fn star_named_expression(&mut self) -> Parsed {
        if self.op("*") {
            return self.bitwise_or();
        }
        self.named_expression()
    }

    fn expression(&mut self) -> Parsed {
        if self.keyword("lambda") {
            return self.lambda();
        }
        self.disjunction()?;
        if self.keyword("if") {
            self.disjunction()?;
            if !self.keyword("else") {
                return None;
            }
            self.expression()?;
        }
        Some(())
    }

    fn lambda(&mut self) -> Parsed {
        // Parameters are not names; their defaults are expressions.
        while !self.op(":") {
            if self.op("*") || self.op("**") {
                if let Some(Token::Name(_)) = self.peek() {
                    self.at += 1;
                }
            } else if self.op("/") {
            } else if let Some(Token::Name(_)) = self.peek() {
                self.at += 1;
                if self.op("=") {
                    self.expression()?;
                }
            } else {
                return None;
            }
            if !self.op(",") && !self.is_op(":") {
                return None;
            }
        }
        self.expression()
    }

    fn disjunction(&mut self) -> Parsed {
        self.conjunction()?;
        while self.keyword("or") {
            self.conjunction()?;
        }
        Some(())
    }

    fn conjunction(&mut self) -> Parsed {
        self.inversion()?;
        while self.keyword("and") {
            self.inversion()?;
        }
        Some(())
    }

    fn inversion(&mut self) -> Parsed {
        if self.keyword("not") {
            return self.inversion();
        }
        self.comparison()
    }

    fn comparison(&mut self) -> Parsed {
        self.bitwise_or()?;
        loop {
            let compare = ["==", "!=", "<=", "<", ">=", ">"]
                .iter()
                .any(|o| self.op(o))
                || self.keyword("in")
                || (self.is_keyword("not")
                    && matches!(self.peek_at(1), Some(Token::Keyword("in")))
                    && {
                        self.at += 2;
                        true
                    })
                || (self.keyword("is") && {
                    self.keyword("not");
                    true
                });
            if !compare {
                return Some(());
            }
            self.bitwise_or()?;
        }
    }

    fn binary(&mut self, ops: &[&str], next: fn(&mut Self) -> Parsed) -> Parsed {
        next(self)?;
        while ops.iter().any(|o| self.op(o)) {
            next(self)?;
        }
        Some(())
    }

    fn bitwise_or(&mut self) -> Parsed {
        self.binary(&["|"], Self::bitwise_xor)
    }

    fn bitwise_xor(&mut self) -> Parsed {
        self.binary(&["^"], Self::bitwise_and)
    }

    fn bitwise_and(&mut self) -> Parsed {
        self.binary(&["&"], Self::shift)
    }

    fn shift(&mut self) -> Parsed {
        self.binary(&["<<", ">>"], Self::sum)
    }

    fn sum(&mut self) -> Parsed {
        self.binary(&["+", "-"], Self::term)
    }

    fn term(&mut self) -> Parsed {
        self.binary(&["*", "/", "//", "%", "@"], Self::factor)
    }

    fn factor(&mut self) -> Parsed {
        if self.op("+") || self.op("-") || self.op("~") {
            return self.factor();
        }
        self.power()
    }

    fn power(&mut self) -> Parsed {
        self.keyword("await");
        self.primary()?;
        if self.op("**") {
            self.factor()?;
        }
        Some(())
    }

    fn primary(&mut self) -> Parsed {
        self.atom()?;
        loop {
            if self.op(".") {
                // An attribute's name is not a Name.
                match self.peek() {
                    Some(Token::Name(_)) => self.at += 1,
                    _ => return None,
                }
            } else if self.op("(") {
                self.arguments()?;
            } else if self.op("[") {
                self.slices()?;
                if !self.op("]") {
                    return None;
                }
            } else {
                return Some(());
            }
        }
    }

    fn arguments(&mut self) -> Parsed {
        let mut keywords = false;
        while !self.op(")") {
            if self.op("**") {
                keywords = true;
                self.expression()?;
            } else if self.op("*") {
                self.expression()?;
            } else if matches!(self.peek(), Some(Token::Name(_)))
                && matches!(self.peek_at(1), Some(Token::Op("=")))
            {
                // A keyword argument's name is not a Name.
                keywords = true;
                self.at += 2;
                self.expression()?;
            } else {
                if keywords {
                    return None;
                }
                self.named_expression()?;
                if self.is_keyword("for") || self.is_keyword("async") {
                    self.comprehension()?;
                }
            }
            if !self.op(",") && !self.is_op(")") {
                return None;
            }
        }
        Some(())
    }

    fn slices(&mut self) -> Parsed {
        loop {
            self.slice()?;
            if !self.op(",") || self.is_op("]") {
                return Some(());
            }
        }
    }

    fn slice(&mut self) -> Parsed {
        if self.op("*") {
            return self.bitwise_or();
        }
        if !self.is_op(":") {
            self.named_expression()?;
        }
        if self.op(":") {
            if !self.is_op(":") && !self.is_op("]") && !self.is_op(",") {
                self.expression()?;
            }
            if self.op(":") && !self.is_op("]") && !self.is_op(",") {
                self.expression()?;
            }
        }
        Some(())
    }

    /// `for target in iter [if cond]…`, after the element.
    fn comprehension(&mut self) -> Parsed {
        while self.is_keyword("for") || self.is_keyword("async") {
            self.keyword("async");
            if !self.keyword("for") {
                return None;
            }
            // The targets are Names too (Store context).
            loop {
                self.star_or_target()?;
                if !self.op(",") || self.is_keyword("in") {
                    break;
                }
            }
            if !self.keyword("in") {
                return None;
            }
            self.disjunction()?;
            while self.keyword("if") {
                self.disjunction()?;
            }
        }
        Some(())
    }

    fn star_or_target(&mut self) -> Parsed {
        self.op("*");
        self.primary()
    }

    fn atom(&mut self) -> Parsed {
        match self.peek().cloned()? {
            Token::Name(name) => {
                self.at += 1;
                self.names.push(name);
                Some(())
            }
            Token::Keyword("None" | "True" | "False") | Token::Number => {
                self.at += 1;
                Some(())
            }
            Token::Literal | Token::FString(_) => {
                // Adjacent strings join into one; bytes may not join text, which
                // is left alone here (no project holds such an input).
                while let Some(Token::Literal | Token::FString(_)) = self.peek().cloned() {
                    if let Some(Token::FString(names)) = self.peek().cloned() {
                        self.names.extend(names);
                    }
                    self.at += 1;
                }
                Some(())
            }
            Token::Op("...") => {
                self.at += 1;
                Some(())
            }
            Token::Op("(") => {
                self.at += 1;
                self.group()
            }
            Token::Op("[") => {
                self.at += 1;
                self.display("]")
            }
            Token::Op("{") => {
                self.at += 1;
                self.braces()
            }
            _ => None,
        }
    }

    /// After `(`: `()`, `(yield …)`, `(x)`, `(x,)`, `(x, y)` or `(x for …)`.
    fn group(&mut self) -> Parsed {
        if self.op(")") {
            return Some(());
        }
        if self.keyword("yield") {
            if self.keyword("from") {
                self.expression()?;
            } else if !self.is_op(")") {
                self.expressions()?;
            }
            return self.op(")").then_some(());
        }
        self.display(")")
    }

    /// The inside of `(…)` or `[…]` after the opening bracket: elements, or a comprehension.
    fn display(&mut self, close: &str) -> Parsed {
        if self.op(close) {
            return Some(());
        }
        let starred = self.is_op("*");
        self.star_named_expression()?;
        if !starred && (self.is_keyword("for") || self.is_keyword("async")) {
            self.comprehension()?;
            return self.op(close).then_some(());
        }
        let mut single = true;
        while self.op(",") {
            single = false;
            if self.is_op(close) {
                break;
            }
            self.star_named_expression()?;
        }
        // `(*a)` alone is not an expression; `[*a]` and `(*a,)` are.
        if starred && single && close == ")" {
            return None;
        }
        self.op(close).then_some(())
    }

    /// After `{`: a dict, a set, or either's comprehension.
    fn braces(&mut self) -> Parsed {
        if self.op("}") {
            return Some(());
        }
        let first_double = self.is_op("**");
        let first_single = self.is_op("*");
        if first_double {
            self.at += 1;
            self.bitwise_or()?;
        } else {
            self.star_named_expression()?;
        }
        let dict = first_double || (!first_single && self.op(":"));
        if dict && !first_double {
            self.expression()?;
        }
        if !first_double && !first_single && (self.is_keyword("for") || self.is_keyword("async")) {
            self.comprehension()?;
            return self.op("}").then_some(());
        }
        while self.op(",") {
            if self.is_op("}") {
                break;
            }
            if dict {
                if self.op("**") {
                    self.bitwise_or()?;
                } else {
                    self.expression()?;
                    if !self.op(":") {
                        return None;
                    }
                    self.expression()?;
                }
            } else {
                self.star_named_expression()?;
            }
        }
        self.op("}").then_some(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn check(file: &str) {
        let path = format!("{}/tests/data/{file}", env!("CARGO_MANIFEST_DIR"));
        let cases: Vec<(String, Vec<String>)> =
            serde_json::from_str::<Vec<Value>>(&std::fs::read_to_string(path).unwrap())
                .unwrap()
                .into_iter()
                .map(|case| serde_json::from_value(case).unwrap())
                .collect();
        let wrong: Vec<_> = cases
            .iter()
            .filter(|(text, names)| dependencies(text).into_iter().collect::<Vec<_>>() != *names)
            .map(|(text, names)| {
                format!("{text:?}: python {names:?}, rust {:?}", dependencies(text))
            })
            .collect();
        assert!(
            wrong.is_empty(),
            "{} of {} differ:\n{}",
            wrong.len(),
            cases.len(),
            wrong.join("\n")
        );
    }

    #[test]
    fn every_string_input_of_the_desktop_projects_names_what_python_says() {
        check("dependencies.json");
    }

    #[test]
    fn random_expressions_name_what_python_says() {
        check("dependencies_fuzz.json");
    }
}
