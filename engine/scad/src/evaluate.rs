//! Evaluating expressions as the Python app does (`simplecad/core/params.py`
//! `evaluate`, `ParameterSet`): a literal with its unit read directly, anything
//! else parsed as a Python expression after unit rewriting and walked over the
//! few node types allowed, with Python's float arithmetic and `math` functions.
//! `tests/data/evaluate.json` holds Python's answers: every input of the Desktop
//! projects, edge cases and random arithmetic, in each dimension.

use crate::params::{Token, lex, tokenize_units, unit_factor};
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// `units.Dimension`: what a value measures, which units it may be written in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dimension {
    Length,
    Angle,
    Scalar,
}

impl Dimension {
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "length" => Some(Self::Length),
            "angle" => Some(Self::Angle),
            "scalar" => Some(Self::Scalar),
            _ => None,
        }
    }
}

/// Why an expression has no value.
#[derive(Clone, Debug, PartialEq)]
pub enum EvalError {
    /// `ExpressionError`: the message the user sees.
    Expression(String),
    /// Another Python exception, which escapes `evaluate` there: its type and message.
    Raised(&'static str, String),
}

impl std::fmt::Display for EvalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Expression(message) => f.write_str(message),
            Self::Raised(kind, message) => write!(f, "{kind}: {message}"),
        }
    }
}

type Result<T> = std::result::Result<T, EvalError>;

fn expression_error(message: impl Into<String>) -> EvalError {
    EvalError::Expression(message.into())
}

/// `evaluate`: `expression`'s value in mm, degrees or plain, `names` its parameters.
pub fn evaluate(
    expression: &str,
    names: &HashMap<String, f64>,
    dimension: Dimension,
) -> Result<f64> {
    let text = expression.trim_matches(char::is_whitespace);
    if text.is_empty() {
        return Err(expression_error("Empty expression"));
    }
    if let Some(value) = parse_quantity(text, dimension) {
        return Ok(value);
    }
    let invalid = || expression_error(format!("{} is not a valid expression", repr_str(text)));
    let tokens = lex(&tokenize_units(text)).ok_or_else(invalid)?;
    if !crate::params::parses(tokens.clone()) {
        return Err(invalid());
    }
    let mut parser = Parser { tokens, at: 0 };
    let tree = parser.expressions();
    walk(&tree, names)
}

/// `units.parse_quantity`: a bare literal with an optional unit (`12.5`, `12.5 mm`, `0.5"`).
fn parse_quantity(text: &str, dimension: Dimension) -> Option<f64> {
    let value_end = text
        .find(|c: char| !"-+0123456789.eE".contains(c))
        .unwrap_or(text.len());
    if value_end == 0 {
        return None;
    }
    let rest = text[value_end..].trim_start_matches(char::is_whitespace);
    let unit_end = rest
        .find(|c: char| !(c.is_ascii_alphabetic() || c == '°' || c == '"'))
        .unwrap_or(rest.len());
    let (unit, tail) = rest.split_at(unit_end);
    if !tail.trim_matches(char::is_whitespace).is_empty() {
        return None;
    }
    let value: f64 = text[..value_end].parse().ok()?;
    if unit.is_empty() {
        return Some(value);
    }
    Some(value * unit_factor(unit, Some(dimension))?)
}

/// The parts of Python's expression tree `_walk` tells apart.
#[derive(Debug)]
enum Node {
    Number(f64),
    /// An integer literal too large for a float.
    Huge,
    /// A constant that is not a number, by its `repr`.
    Constant(String),
    Name(String),
    Binary(&'static str, Box<Node>, Box<Node>),
    Unary(&'static str, Box<Node>),
    /// The function, its positional arguments (`None` for `*args`), and whether it had keywords.
    Call(Box<Node>, Vec<Option<Node>>, bool),
    /// Anything else: a comparison, a tuple, a lambda...
    Other,
}

/// A recursive descent over Python's expression grammar for text Python
/// accepts (checked first), keeping what `_walk` evaluates.
struct Parser {
    tokens: Vec<Token>,
    at: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.at)
    }

    fn op(&mut self, op: &str) -> bool {
        let found = matches!(self.peek(), Some(Token::Op(o)) if *o == op);
        self.at += usize::from(found);
        found
    }

    fn keyword(&mut self, word: &str) -> bool {
        let found = matches!(self.peek(), Some(Token::Keyword(k)) if *k == word);
        self.at += usize::from(found);
        found
    }

    fn is_op(&self, ops: &[&str]) -> Option<&'static str> {
        match self.peek() {
            Some(Token::Op(o)) if ops.contains(o) => Some(o),
            _ => None,
        }
    }

    /// Skips to the end of the current expression: a `,`, closing bracket or the end, at this depth.
    fn skip_expression(&mut self) {
        let mut depth = 0usize;
        while let Some(token) = self.peek() {
            match token {
                Token::Op("(" | "[" | "{") => depth += 1,
                Token::Op(")" | "]" | "}") if depth == 0 => return,
                Token::Op(")" | "]" | "}") => depth -= 1,
                Token::Op(",") if depth == 0 => return,
                _ => {}
            }
            self.at += 1;
        }
    }

    /// Skips a bracketed group whose opening bracket is next.
    fn skip_group(&mut self) {
        let mut depth = 0usize;
        while let Some(token) = self.peek().cloned() {
            self.at += 1;
            match token {
                Token::Op("(" | "[" | "{") => depth += 1,
                Token::Op(")" | "]" | "}") => {
                    depth -= 1;
                    if depth == 0 {
                        return;
                    }
                }
                _ => {}
            }
        }
    }

    /// `expressions`: a top-level tuple is not a value.
    fn expressions(&mut self) -> Node {
        if self.is_op(&["*"]).is_some() {
            return Node::Other;
        }
        let first = self.expression();
        if self.op(",") {
            return Node::Other;
        }
        first
    }

    fn expression(&mut self) -> Node {
        if self.keyword("lambda") {
            self.skip_expression();
            return Node::Other;
        }
        // `name := value`
        if matches!(self.peek(), Some(Token::Name(_)))
            && matches!(self.tokens.get(self.at + 1), Some(Token::Op(":=")))
        {
            self.skip_expression();
            return Node::Other;
        }
        let body = self.disjunction();
        if self.keyword("if") {
            self.disjunction();
            self.keyword("else");
            self.expression();
            return Node::Other;
        }
        body
    }

    fn disjunction(&mut self) -> Node {
        let first = self.conjunction();
        let mut boolean = false;
        while self.keyword("or") {
            self.conjunction();
            boolean = true;
        }
        if boolean { Node::Other } else { first }
    }

    fn conjunction(&mut self) -> Node {
        let first = self.inversion();
        let mut boolean = false;
        while self.keyword("and") {
            self.inversion();
            boolean = true;
        }
        if boolean { Node::Other } else { first }
    }

    fn inversion(&mut self) -> Node {
        if self.keyword("not") {
            return Node::Unary("not", Box::new(self.inversion()));
        }
        self.comparison()
    }

    fn comparison(&mut self) -> Node {
        let first = self.binary(0);
        let mut compared = false;
        loop {
            let operator = self.is_op(&["<", ">", "==", ">=", "<=", "!="]).is_some()
                || matches!(self.peek(), Some(Token::Keyword("in" | "is")))
                || (matches!(self.peek(), Some(Token::Keyword("not")))
                    && matches!(self.tokens.get(self.at + 1), Some(Token::Keyword("in"))));
            if !operator {
                break;
            }
            if self.keyword("not") || self.keyword("is") {
                self.keyword("not");
                self.keyword("in");
            } else {
                self.at += 1;
            }
            self.binary(0);
            compared = true;
        }
        if compared { Node::Other } else { first }
    }

    /// The binary operators from `|` down to `*`, loosest first.
    fn binary(&mut self, level: usize) -> Node {
        const LEVELS: &[&[&str]] = &[
            &["|"],
            &["^"],
            &["&"],
            &["<<", ">>"],
            &["+", "-"],
            &["*", "/", "//", "%", "@"],
        ];
        if level == LEVELS.len() {
            return self.factor();
        }
        let mut left = self.binary(level + 1);
        while let Some(op) = self.is_op(LEVELS[level]) {
            self.at += 1;
            let right = self.binary(level + 1);
            left = Node::Binary(op, Box::new(left), Box::new(right));
        }
        left
    }

    fn factor(&mut self) -> Node {
        if let Some(op) = self.is_op(&["-", "+", "~"]) {
            self.at += 1;
            return Node::Unary(op, Box::new(self.factor()));
        }
        self.power()
    }

    fn power(&mut self) -> Node {
        let base = if self.keyword("await") {
            self.primary();
            Node::Other
        } else {
            self.primary()
        };
        if self.op("**") {
            return Node::Binary("**", Box::new(base), Box::new(self.factor()));
        }
        base
    }

    fn primary(&mut self) -> Node {
        let mut node = self.atom();
        loop {
            if self.op(".") {
                self.at += 1;
                node = Node::Other;
            } else if self.is_op(&["["]).is_some() {
                self.skip_group();
                node = Node::Other;
            } else if self.op("(") {
                node = self.call(node);
            } else {
                return node;
            }
        }
    }

    /// The arguments of a call, its `(` read.
    fn call(&mut self, function: Node) -> Node {
        let mut args = Vec::new();
        let mut keywords = false;
        while !self.op(")") {
            if self.op("**") {
                self.skip_expression();
                keywords = true;
            } else if self.op("*") {
                self.skip_expression();
                args.push(None);
            } else if matches!(self.peek(), Some(Token::Name(_)))
                && matches!(self.tokens.get(self.at + 1), Some(Token::Op("=")))
            {
                self.skip_expression();
                keywords = true;
            } else {
                let start = self.at;
                let arg = self.expression();
                // A generator expression argument, `f(x for x in y)`.
                if matches!(self.peek(), Some(Token::Keyword("for" | "async"))) {
                    self.at = start;
                    self.skip_expression();
                    args.push(Some(Node::Other));
                } else {
                    args.push(Some(arg));
                }
            }
            self.op(",");
        }
        Node::Call(Box::new(function), args, keywords)
    }

    fn atom(&mut self) -> Node {
        let Some(token) = self.peek().cloned() else {
            return Node::Other;
        };
        match token {
            Token::Name(name) => {
                self.at += 1;
                Node::Name(name)
            }
            Token::Number(text) => {
                self.at += 1;
                number(&text)
            }
            Token::Keyword(word @ ("True" | "False" | "None")) => {
                self.at += 1;
                Node::Constant(word.to_string())
            }
            Token::Op("...") => {
                self.at += 1;
                Node::Constant("Ellipsis".into())
            }
            Token::Literal(_) | Token::FString(_) => {
                let mut pieces = Vec::new();
                let mut formatted = false;
                while let Some(Token::Literal(_) | Token::FString(_)) = self.peek() {
                    match self.peek() {
                        Some(Token::Literal(raw)) => pieces.push(raw.clone()),
                        _ => formatted = true,
                    }
                    self.at += 1;
                }
                if formatted {
                    Node::Other
                } else {
                    Node::Constant(strings_repr(&pieces))
                }
            }
            Token::Op("(") => self.group(),
            _ => {
                // A list, set or dict, or their comprehensions.
                self.skip_group();
                Node::Other
            }
        }
    }

    /// `( ... )`: one expression in parentheses is that expression; a tuple,
    /// generator or `yield` is not a value.
    fn group(&mut self) -> Node {
        let start = self.at;
        self.at += 1;
        if self.op(")")
            || matches!(self.peek(), Some(Token::Keyword("yield")))
            || self.is_op(&["*"]).is_some()
        {
            self.at = start;
            self.skip_group();
            return Node::Other;
        }
        let inner = self.expression();
        if self.op(")") {
            return inner;
        }
        self.at = start;
        self.skip_group();
        Node::Other
    }
}

/// A number literal's value; an imaginary one is a constant that is not a number.
fn number(text: &str) -> Node {
    let digits: String = text.chars().filter(|c| *c != '_').collect();
    let lower = digits.to_ascii_lowercase();
    if let Some(imaginary) = lower.strip_suffix('j') {
        let value: f64 = imaginary.parse().unwrap_or(f64::NAN);
        let repr = crate::pyjson::float_repr(value);
        return Node::Constant(format!("{}j", repr.strip_suffix(".0").unwrap_or(&repr)));
    }
    let radix = match lower.get(..2) {
        Some("0x") => 16,
        Some("0o") => 8,
        Some("0b") => 2,
        _ => 10,
    };
    if radix != 10 {
        // ponytail: past 128 bits a based literal is taken as too large; Python
        // would round it to a float up to 2**1024.
        return u128::from_str_radix(&lower[2..], radix)
            .map_or(Node::Huge, |v| Node::Number(v as f64));
    }
    match lower.parse::<f64>() {
        // A decimal integer converts with correct rounding, as `float(int)` does, or overflows.
        Ok(v) if v.is_infinite() && !lower.contains(['.', 'e']) => Node::Huge,
        Ok(v) => Node::Number(v),
        Err(_) => Node::Other,
    }
}

/// Python's `repr` of a float, as in its messages (`-1.0`, `inf`).
fn repr_float(value: f64) -> String {
    if value.is_nan() {
        "nan".into()
    } else if value.is_infinite() {
        if value > 0.0 {
            "inf".into()
        } else {
            "-inf".into()
        }
    } else {
        crate::pyjson::float_repr(value)
    }
}

/// Python's `repr` of a str: single quotes unless only double ones are safe.
fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// The `repr` of adjacent string literals joined, as Python's parser joins them.
///
/// ponytail: escapes other than the common ones (`\n`, `\t`, quotes, `\\`, `\xNN`)
/// are kept as written; no project has a quoted string in an expression yet.
fn strings_repr(pieces: &[String]) -> String {
    let mut value = String::new();
    let mut bytes = false;
    for raw in pieces {
        let prefix_end = raw.find(['\'', '"']).unwrap_or(0);
        let prefix = raw[..prefix_end].to_ascii_lowercase();
        bytes = prefix.contains('b');
        let body = &raw[prefix_end..];
        let quote_len = if body.starts_with("'''") || body.starts_with("\"\"\"") {
            3
        } else {
            1
        };
        let content = &body[quote_len..body.len() - quote_len];
        if prefix.contains('r') {
            value.push_str(content);
            continue;
        }
        let mut chars = content.chars().peekable();
        while let Some(c) = chars.next() {
            if c != '\\' {
                value.push(c);
                continue;
            }
            match chars.next() {
                Some('n') => value.push('\n'),
                Some('t') => value.push('\t'),
                Some('r') => value.push('\r'),
                Some('0') => value.push('\0'),
                Some('\n') => {}
                Some(q @ ('\\' | '\'' | '"')) => value.push(q),
                Some('x') => {
                    let hex: String = chars.by_ref().take(2).collect();
                    value.push(
                        u32::from_str_radix(&hex, 16)
                            .ok()
                            .and_then(char::from_u32)
                            .unwrap_or('?'),
                    );
                }
                Some(other) => {
                    value.push('\\');
                    value.push(other);
                }
                None => value.push('\\'),
            }
        }
    }
    if bytes {
        format!("b{}", repr_str(&value))
    } else {
        repr_str(&value)
    }
}

const FUNCTIONS: &[&str] = &[
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "abs", "min", "max", "floor",
    "ceil", "round", "hypot", "log", "exp",
];

/// `_walk`.
fn walk(node: &Node, names: &HashMap<String, f64>) -> Result<f64> {
    match node {
        Node::Number(value) => Ok(*value),
        Node::Huge => Err(EvalError::Raised(
            "OverflowError",
            "int too large to convert to float".into(),
        )),
        Node::Constant(repr) => Err(expression_error(format!("{repr} is not a number"))),
        Node::Name(name) => match names.get(name) {
            Some(value) => Ok(*value),
            None => match name.as_str() {
                "pi" => Ok(std::f64::consts::PI),
                "e" => Ok(std::f64::consts::E),
                "tau" => Ok(std::f64::consts::TAU),
                _ => Err(expression_error(format!("Unknown name {}", repr_str(name)))),
            },
        },
        Node::Binary(op, left, right) => {
            if !["+", "-", "*", "/", "//", "%", "**"].contains(op) {
                return Err(expression_error("Unsupported operator"));
            }
            let (a, b) = (walk(left, names)?, walk(right, names)?);
            let zero = || expression_error("Division by zero");
            match *op {
                "+" => Ok(a + b),
                "-" => Ok(a - b),
                "*" => Ok(a * b),
                "/" if b == 0.0 => Err(zero()),
                "/" => Ok(a / b),
                "//" if b == 0.0 => Err(zero()),
                "//" => Ok(div_mod(a, b).0),
                "%" if b == 0.0 => Err(zero()),
                "%" => Ok(remainder(a, b)),
                _ => power(a, b),
            }
        }
        Node::Unary(op, operand) => match *op {
            "-" => Ok(-walk(operand, names)?),
            "+" => walk(operand, names),
            _ => Err(expression_error("Unsupported unary operator")),
        },
        Node::Call(function, args, keywords) => {
            let name = match function.as_ref() {
                Node::Name(name) if FUNCTIONS.contains(&name.as_str()) => name,
                _ => return Err(expression_error("Unknown function")),
            };
            if *keywords {
                return Err(expression_error("Functions take positional arguments only"));
            }
            let mut values = Vec::with_capacity(args.len());
            for arg in args {
                match arg {
                    Some(arg) => values.push(walk(arg, names)?),
                    None => {
                        return Err(expression_error(
                            "Expression contains something that is not allowed",
                        ));
                    }
                }
            }
            call(name, &values).map_err(|error| match error {
                Call::Caught(message) => expression_error(format!("{name}: {message}")),
                Call::Raised(kind, message) => EvalError::Raised(kind, message),
            })
        }
        Node::Other => Err(expression_error(
            "Expression contains something that is not allowed",
        )),
    }
}

/// CPython's `_float_div_mod`: the floor quotient and the remainder with the divisor's sign.
fn div_mod(a: f64, b: f64) -> (f64, f64) {
    let mut modulo = a % b;
    let mut div = (a - modulo) / b;
    if modulo != 0.0 {
        if (b < 0.0) != (modulo < 0.0) {
            modulo += b;
            div -= 1.0;
        }
    } else {
        modulo = 0.0f64.copysign(b);
    }
    let floor = if div != 0.0 {
        let mut floor = div.floor();
        if div - floor > 0.5 {
            floor += 1.0;
        }
        floor
    } else {
        0.0f64.copysign(a / b)
    };
    (floor, modulo)
}

/// CPython's `float_rem`.
fn remainder(a: f64, b: f64) -> f64 {
    let modulo = a % b;
    if modulo != 0.0 {
        if (b < 0.0) != (modulo < 0.0) {
            modulo + b
        } else {
            modulo
        }
    } else {
        0.0f64.copysign(b)
    }
}

fn is_odd_integer(x: f64) -> bool {
    x.abs() % 2.0 == 1.0
}

/// CPython's `float_pow`, then `float()` of the result.
fn power(base: f64, exponent: f64) -> Result<f64> {
    if exponent == 0.0 {
        return Ok(1.0);
    }
    if base.is_nan() {
        return Ok(base);
    }
    if exponent.is_nan() {
        return Ok(if base == 1.0 { 1.0 } else { exponent });
    }
    if exponent.is_infinite() {
        let magnitude = base.abs();
        return Ok(if magnitude == 1.0 {
            1.0
        } else if (exponent > 0.0) == (magnitude > 1.0) {
            exponent.abs()
        } else {
            0.0
        });
    }
    if base.is_infinite() {
        let odd = is_odd_integer(exponent);
        return Ok(if exponent > 0.0 {
            if odd { base } else { base.abs() }
        } else if odd {
            0.0f64.copysign(base)
        } else {
            0.0
        });
    }
    if base == 0.0 {
        if exponent < 0.0 {
            return Err(expression_error("Division by zero"));
        }
        return Ok(if is_odd_integer(exponent) { base } else { 0.0 });
    }
    let (mut base, mut negate) = (base, false);
    if base < 0.0 {
        if exponent != exponent.floor() {
            // A complex result (`_Py_c_pow`), which overflows or which `float()` refuses.
            let length = (-base).powf(exponent);
            let phase = std::f64::consts::PI * exponent;
            if (length * phase.cos()).is_infinite() || (length * phase.sin()).is_infinite() {
                return Err(EvalError::Raised(
                    "OverflowError",
                    "complex exponentiation".into(),
                ));
            }
            return Err(EvalError::Raised(
                "TypeError",
                "float() argument must be a string or a real number, not 'complex'".into(),
            ));
        }
        base = -base;
        negate = is_odd_integer(exponent);
    }
    if base == 1.0 {
        return Ok(if negate { -1.0 } else { 1.0 });
    }
    let result = base.powf(exponent);
    if result.is_infinite() {
        return Err(EvalError::Raised(
            "OverflowError",
            "(34, 'Numerical result out of range')".into(),
        ));
    }
    Ok(if negate { -result } else { result })
}

enum Call {
    /// `TypeError` or `ValueError`: `_walk` reports it as `name: message`.
    Caught(String),
    Raised(&'static str, String),
}

/// `FUNCTIONS[name](*args)`, with the messages Python 3.14 gives for wrong calls.
fn call(name: &str, args: &[f64]) -> std::result::Result<f64, Call> {
    let caught = |m: String| Err(Call::Caught(m));
    let n = args.len();
    // The trigonometry is wrapped in lambdas with these parameters.
    let lambda = |params: &[&str]| -> std::result::Result<(), Call> {
        let quoted: Vec<String> = params.iter().map(|p| format!("'{p}'")).collect();
        if n > params.len() {
            let s = if params.len() == 1 { "" } else { "s" };
            return Err(Call::Caught(format!(
                "<lambda>() takes {} positional argument{s} but {n} were given",
                params.len()
            )));
        }
        if n < params.len() {
            let missing = &quoted[n..];
            let s = if missing.len() == 1 { "" } else { "s" };
            let list = match missing {
                [one] => one.clone(),
                [a, b] => format!("{a} and {b}"),
                many => format!(
                    "{} and {}",
                    many[..many.len() - 1].join(", "),
                    many[many.len() - 1]
                ),
            };
            return Err(Call::Caught(format!(
                "<lambda>() missing {} required positional argument{s}: {list}",
                missing.len()
            )));
        }
        Ok(())
    };
    let one = |label: &str| -> std::result::Result<f64, Call> {
        if n == 1 {
            Ok(args[0])
        } else {
            Err(Call::Caught(format!(
                "{label}() takes exactly one argument ({n} given)"
            )))
        }
    };
    let finite = |x: f64| -> std::result::Result<f64, Call> {
        if x.is_infinite() {
            Err(Call::Caught(format!(
                "expected a finite input, got {}",
                repr_float(x)
            )))
        } else {
            Ok(x)
        }
    };
    let unit_range = |x: f64| -> std::result::Result<f64, Call> {
        if x.abs() > 1.0 {
            Err(Call::Caught(format!(
                "expected a number in range from -1 up to 1, got {}",
                repr_float(x)
            )))
        } else {
            Ok(x)
        }
    };
    let to_integer = |x: f64| -> std::result::Result<f64, Call> {
        if x.is_nan() {
            Err(Call::Caught("cannot convert float NaN to integer".into()))
        } else if x.is_infinite() {
            Err(Call::Raised(
                "OverflowError",
                "cannot convert float infinity to integer".into(),
            ))
        } else {
            // An integer has no sign of zero.
            Ok(x + 0.0)
        }
    };
    let positive = |x: f64| -> std::result::Result<f64, Call> {
        if x.is_nan() || x > 0.0 {
            Ok(x.ln())
        } else {
            Err(Call::Caught(format!(
                "expected a positive input, got {}",
                repr_float(x)
            )))
        }
    };
    match name {
        "sin" | "cos" | "tan" => {
            lambda(&["d"])?;
            let r = finite(args[0].to_radians())?;
            Ok(match name {
                "sin" => r.sin(),
                "cos" => r.cos(),
                _ => r.tan(),
            })
        }
        "asin" | "acos" | "atan" => {
            lambda(&["x"])?;
            let x = args[0];
            Ok(match name {
                "asin" => unit_range(x)?.asin(),
                "acos" => unit_range(x)?.acos(),
                _ => x.atan(),
            }
            .to_degrees())
        }
        "atan2" => {
            lambda(&["y", "x"])?;
            Ok(args[0].atan2(args[1]).to_degrees())
        }
        "sqrt" => {
            let x = one("math.sqrt")?;
            if x < 0.0 {
                return caught(format!(
                    "expected a nonnegative input, got {}",
                    repr_float(x)
                ));
            }
            Ok(x.sqrt())
        }
        "abs" => Ok(one("abs")?.abs()),
        "min" | "max" => match n {
            0 => caught(format!("{name} expected at least 1 argument, got 0")),
            1 => caught("'float' object is not iterable".into()),
            _ => Ok(args[1..].iter().fold(args[0], |best, &x| {
                if (name == "min" && x < best) || (name == "max" && x > best) {
                    x
                } else {
                    best
                }
            })),
        },
        "floor" => to_integer(one("math.floor")?.floor()),
        "ceil" => to_integer(one("math.ceil")?.ceil()),
        "round" => match n {
            0 => caught("round() missing required argument 'number' (pos 1)".into()),
            1 => to_integer(args[0].round_ties_even()),
            2 => caught("'float' object cannot be interpreted as an integer".into()),
            _ => caught(format!("round() takes at most 2 arguments ({n} given)")),
        },
        "hypot" => Ok(hypot(args)),
        "log" => match n {
            0 => caught("log expected at least 1 argument, got 0".into()),
            1 => Ok(positive(args[0])?),
            2 => {
                let (num, den) = (positive(args[0])?, positive(args[1])?);
                if den == 0.0 {
                    return Err(Call::Raised("ZeroDivisionError", "division by zero".into()));
                }
                Ok(num / den)
            }
            _ => caught(format!("log expected at most 2 arguments, got {n}")),
        },
        _ => {
            let x = one("math.exp")?;
            let result = x.exp();
            if result.is_infinite() && x.is_finite() {
                return Err(Call::Raised("OverflowError", "math range error".into()));
            }
            Ok(result)
        }
    }
}

/// CPython's `math.hypot` (`vector_norm`): exact scaling and squaring, a
/// compensated sum and one correction step.
fn hypot(args: &[f64]) -> f64 {
    let mut values: Vec<f64> = args.iter().map(|x| x.abs()).collect();
    let max = values
        .iter()
        .fold(0.0f64, |m, &x| if x > m { x } else { m });
    let found_nan = values.iter().any(|x| x.is_nan());
    vector_norm(&mut values, max, found_nan)
}

fn vector_norm(values: &mut [f64], max: f64, found_nan: bool) -> f64 {
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 || values.len() <= 1 {
        return max;
    }
    let max_e = frexp_exponent(max);
    if max_e < -1023 {
        for v in values.iter_mut() {
            *v /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * vector_norm(values, max / f64::MIN_POSITIVE, found_nan);
    }
    let scale = power_of_two(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    let mul = |x: f64, y: f64| {
        let hi = x * y;
        (hi, x.mul_add(y, -hi))
    };
    let fast_sum = |a: f64, b: f64| {
        let hi = a + b;
        (hi, (a - hi) + b)
    };
    for &v in values.iter() {
        let x = v * scale;
        let (hi, lo) = mul(x, x);
        let (sum, low) = fast_sum(csum, hi);
        csum = sum;
        frac1 += lo;
        frac2 += low;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = mul(-h, h);
    let (sum, low) = fast_sum(csum, hi);
    csum = sum;
    frac1 += lo;
    frac2 += low;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

/// C's `frexp` exponent: `x = m * 2**e` with `0.5 <= m < 1`.
fn frexp_exponent(x: f64) -> i32 {
    let bits = x.to_bits();
    let field = ((bits >> 52) & 0x7ff) as i32;
    if field == 0 {
        return frexp_exponent(x * 2f64.powi(54)) - 54;
    }
    field - 1022
}

/// `ldexp(1.0, e)`, exactly, subnormal results included.
fn power_of_two(e: i32) -> f64 {
    if e >= -1022 {
        f64::from_bits(((e + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (e + 1074))
    }
}

/// `ParameterSet._resolve`: each parameter's value, in dependency order, and
/// the message for each that has none.
pub fn resolve(
    parameters: &[serde_json::Value],
) -> (HashMap<String, f64>, BTreeMap<String, String>) {
    let mut defined: Vec<(String, String, Dimension)> = Vec::new();
    for p in parameters {
        let (Some(name), Some(expression)) = (p["name"].as_str(), p["expression"].as_str()) else {
            continue;
        };
        let dimension = p["dimension"]
            .as_str()
            .and_then(Dimension::from_name)
            .unwrap_or(Dimension::Length);
        // A repeated name: the last definition, in the first one's place.
        match defined.iter_mut().find(|(n, _, _)| n == name) {
            Some(slot) => *slot = (name.into(), expression.into(), dimension),
            None => defined.push((name.into(), expression.into(), dimension)),
        }
    }
    let known: BTreeSet<&str> = defined.iter().map(|(n, _, _)| n.as_str()).collect();
    let mut remaining: BTreeMap<String, BTreeSet<String>> = defined
        .iter()
        .map(|(n, e, _)| {
            let deps = crate::params::dependencies(e)
                .into_iter()
                .filter(|d| known.contains(d.as_str()))
                .collect();
            (n.clone(), deps)
        })
        .collect();
    let mut order = Vec::new();
    loop {
        let ready: Vec<String> = remaining
            .iter()
            .filter(|(_, d)| d.is_empty())
            .map(|(n, _)| n.clone())
            .collect();
        if ready.is_empty() {
            break;
        }
        for name in &ready {
            remaining.remove(name);
        }
        for deps in remaining.values_mut() {
            deps.retain(|d| !ready.contains(d));
        }
        order.extend(ready);
    }
    let mut values = HashMap::new();
    let mut errors = BTreeMap::new();
    for name in remaining.keys() {
        errors.insert(
            name.clone(),
            format!(
                "{} refers back to itself through another parameter.",
                repr_str(name)
            ),
        );
    }
    for name in order {
        let (_, expression, dimension) = defined.iter().find(|(n, _, _)| *n == name).unwrap();
        match evaluate(expression, &values, *dimension) {
            Ok(value) => {
                values.insert(name, value);
            }
            Err(EvalError::Expression(message)) => {
                errors.insert(name, message);
            }
            Err(raised) => {
                errors.insert(name, raised.to_string());
            }
        }
    }
    (values, errors)
}
