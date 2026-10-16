use std::fmt;

const MAX_SEQUENCE: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    Incomplete,
    Unsupported,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Code {
    Character(char),
    Named(&'static str),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
struct Modifiers(u8);

impl Modifiers {
    const SHIFT: u8 = 1;
    const ALT: u8 = 2;
    const CONTROL: u8 = 4;

    fn xterm(value: u32) -> Result<Self, DecodeError> {
        match value {
            1..=8 => Ok(Self((value - 1) as u8)),
            _ => Err(DecodeError::Unsupported),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Key {
    code: Code,
    modifiers: Modifiers,
}

impl Key {
    pub fn named(name: &'static str) -> Self {
        Self {
            code: Code::Named(name),
            modifiers: Modifiers::default(),
        }
    }

    pub fn character(value: u32) -> Result<Self, DecodeError> {
        let name = match value {
            9 => Some("Tab"),
            13 => Some("Enter"),
            27 => Some("Escape"),
            127 => Some("BSpace"),
            _ => None,
        };
        if let Some(name) = name {
            return Ok(Self::named(name));
        }
        if value < 32 {
            return Ok(Self {
                code: Code::Character(char::from_u32(value + 64).unwrap().to_ascii_lowercase()),
                modifiers: Modifiers(Modifiers::CONTROL),
            });
        }
        let ch = char::from_u32(value).ok_or(DecodeError::Unsupported)?;
        if ch.is_control() {
            return Err(DecodeError::Unsupported);
        }
        Ok(Self {
            code: Code::Character(ch),
            modifiers: Modifiers::default(),
        })
    }

    fn modified(mut self, value: u32) -> Result<Self, DecodeError> {
        self.modifiers.0 |= Modifiers::xterm(value)?.0;
        Ok(self)
    }

    pub fn is_escape(self) -> bool {
        self == Self::named("Escape")
    }
}

impl fmt::Display for Key {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (bit, prefix) in [
            (Modifiers::CONTROL, "C-"),
            (Modifiers::ALT, "M-"),
            (Modifiers::SHIFT, "S-"),
        ] {
            if self.modifiers.0 & bit != 0 {
                out.write_str(prefix)?;
            }
        }
        match self.code {
            Code::Character(ch) => write!(out, "{ch}"),
            Code::Named(name) => out.write_str(name),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Decoded {
    pub key: Option<Key>,
    pub consumed: usize,
}

fn navigation(final_byte: u8) -> Option<&'static str> {
    match final_byte {
        b'A' => Some("Up"),
        b'B' => Some("Down"),
        b'C' => Some("Right"),
        b'D' => Some("Left"),
        b'H' => Some("Home"),
        b'F' => Some("End"),
        b'P' => Some("F1"),
        b'Q' => Some("F2"),
        b'R' => Some("F3"),
        b'S' => Some("F4"),
        _ => None,
    }
}

fn tilde(value: u32) -> Option<&'static str> {
    match value {
        1 | 7 => Some("Home"),
        2 => Some("Insert"),
        3 => Some("Delete"),
        4 | 8 => Some("End"),
        5 => Some("PPage"),
        6 => Some("NPage"),
        11 => Some("F1"),
        12 => Some("F2"),
        13 => Some("F3"),
        14 => Some("F4"),
        15 => Some("F5"),
        17 => Some("F6"),
        18 => Some("F7"),
        19 => Some("F8"),
        20 => Some("F9"),
        21 => Some("F10"),
        23 => Some("F11"),
        24 => Some("F12"),
        _ => None,
    }
}

fn ss3(final_byte: u8) -> Option<&'static str> {
    navigation(final_byte).or(match final_byte {
        b'p' => Some("KP0"),
        b'q' => Some("KP1"),
        b'r' => Some("KP2"),
        b's' => Some("KP3"),
        b't' => Some("KP4"),
        b'u' => Some("KP5"),
        b'v' => Some("KP6"),
        b'w' => Some("KP7"),
        b'x' => Some("KP8"),
        b'y' => Some("KP9"),
        b'M' => Some("KPEnter"),
        b'n' => Some("KP."),
        b'o' => Some("KP/"),
        b'j' => Some("KP*"),
        b'm' => Some("KP-"),
        b'k' => Some("KP+"),
        _ => None,
    })
}

fn parameters(bytes: &[u8]) -> Result<Vec<u32>, DecodeError> {
    if bytes.is_empty() {
        return Ok(Vec::new());
    }
    let mut values = Vec::new();
    for part in bytes.split(|byte| *byte == b';') {
        if values.len() == 3 {
            return Err(DecodeError::Unsupported);
        }
        let mut value = 0u32;
        for byte in part {
            let digit = byte
                .checked_sub(b'0')
                .filter(|d| *d <= 9)
                .ok_or(DecodeError::Unsupported)?;
            value = value
                .checked_mul(10)
                .and_then(|v| v.checked_add(u32::from(digit)))
                .ok_or(DecodeError::Unsupported)?;
        }
        values.push(if part.is_empty() { 1 } else { value });
    }
    Ok(values)
}

fn csi(data: &[u8]) -> Result<Decoded, DecodeError> {
    let mouse = data.starts_with(b"\x1b[<");
    let start = if mouse { 3 } else { 2 };
    let mut end = start;
    while let Some(byte) = data.get(end) {
        if end >= MAX_SEQUENCE {
            return Err(DecodeError::Unsupported);
        }
        if byte.is_ascii_digit() || *byte == b';' {
            end += 1;
        } else {
            break;
        }
    }
    let final_byte = *data.get(end).ok_or(if end < MAX_SEQUENCE {
        DecodeError::Incomplete
    } else {
        DecodeError::Unsupported
    })?;
    let params = parameters(&data[start..end])?;
    if mouse {
        if params.len() != 3
            || !matches!(final_byte, b'M' | b'm')
            || data[start..end].split(|b| *b == b';').any(|p| p.is_empty())
        {
            return Err(DecodeError::Unsupported);
        }
        return Ok(Decoded {
            key: None,
            consumed: end + 1,
        });
    }
    let modifier = params.get(1).copied().unwrap_or(1);
    let key = if let Some(name) = navigation(final_byte) {
        if params.len() > 2 || params.first().is_some_and(|p| *p != 1) {
            return Err(DecodeError::Unsupported);
        }
        Key::named(name).modified(modifier)?
    } else {
        match (final_byte, params.as_slice()) {
            (b'Z', []) | (b'Z', [1]) => Key::named("BTab"),
            (b'u', [code]) => Key::character(*code)?,
            (b'u', [code, modifier]) => Key::character(*code)?.modified(*modifier)?,
            (b'~', [27, modifier, code]) => Key::character(*code)?.modified(*modifier)?,
            (b'~', [code]) | (b'~', [code, _]) => {
                Key::named(tilde(*code).ok_or(DecodeError::Unsupported)?).modified(modifier)?
            }
            _ => return Err(DecodeError::Unsupported),
        }
    };
    Ok(Decoded {
        key: Some(key),
        consumed: end + 1,
    })
}

fn plain(data: &[u8]) -> Result<Decoded, DecodeError> {
    let first = *data.first().ok_or(DecodeError::Incomplete)?;
    let length = match first {
        0..=127 => 1,
        194..=223 => 2,
        224..=239 => 3,
        240..=244 => 4,
        _ => return Err(DecodeError::Unsupported),
    };
    let bytes = data.get(..length).ok_or(DecodeError::Incomplete)?;
    let text = std::str::from_utf8(bytes).map_err(|_| DecodeError::Unsupported)?;
    let ch = text.chars().next().ok_or(DecodeError::Unsupported)?;
    Ok(Decoded {
        key: Some(Key::character(u32::from(ch))?),
        consumed: length,
    })
}

pub fn decode(data: &[u8]) -> Result<Decoded, DecodeError> {
    let mut offset = 0;
    // Iteration bounds repeated Escape prefixes without recursive decoding.
    while data.get(offset) == Some(&27) {
        match data.get(offset + 1) {
            None => return Err(DecodeError::Incomplete),
            Some(b'[' | b'O') => break,
            _ if offset < MAX_SEQUENCE => offset += 1,
            _ => return Err(DecodeError::Unsupported),
        }
    }
    let rest = &data[offset..];
    let mut decoded = if rest.starts_with(b"\x1b[") {
        csi(rest)?
    } else if rest.starts_with(b"\x1bO") {
        let byte = *rest.get(2).ok_or(DecodeError::Incomplete)?;
        Decoded {
            key: Some(Key::named(ss3(byte).ok_or(DecodeError::Unsupported)?)),
            consumed: 3,
        }
    } else {
        plain(rest)?
    };
    if offset > 0 {
        decoded.key = decoded.key.map(|mut key| {
            key.modifiers.0 |= Modifiers::ALT;
            key
        });
        decoded.consumed += offset;
    }
    Ok(decoded)
}

pub fn finish(pending: &[u8]) -> Result<Option<Key>, DecodeError> {
    match pending {
        [] => Ok(None),
        b"\x1b" => Ok(Some(Key::named("Escape"))),
        b"\x1b[" => Ok(Some(Key::character(u32::from('['))?.modified(3)?)),
        b"\x1bO" => Ok(Some(Key::character(u32::from('O'))?.modified(3)?)),
        _ => Err(DecodeError::Unsupported),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keyboard_encodings() {
        let cases: &[(&[u8], &str)] = &[
            (b"1", "1"),
            (b";", ";"),
            (b"\0", "C-@"),
            (b"\x02", "C-b"),
            (b"\x03", "C-c"),
            (b"\t", "Tab"),
            (b"\r", "Enter"),
            (b"\x7f", "BSpace"),
            ("é".as_bytes(), "é"),
            ("😺".as_bytes(), "😺"),
            (b"\x1bh", "M-h"),
            (b"\x1b\x02", "C-M-b"),
            (b"\x1b;", "M-;"),
            (b"\x1b[A", "Up"),
            (b"\x1bOD", "Left"),
            (b"\x1b[1;5D", "C-Left"),
            (b"\x1b[1;3C", "M-Right"),
            (b"\x1b[1;2A", "S-Up"),
            (b"\x1b[3~", "Delete"),
            (b"\x1b[6~", "NPage"),
            (b"\x1b[Z", "BTab"),
            (b"\x1bOP", "F1"),
            (b"\x1b[24~", "F12"),
            (b"\x1b[15;5~", "C-F5"),
            (b"\x1bOp", "KP0"),
            (b"\x1bOM", "KPEnter"),
            (b"\x1b[65;6u", "C-S-A"),
            (b"\x1b[27;3;59~", "M-;"),
        ];
        for (raw, expected) in cases {
            let mut with_tail = raw.to_vec();
            with_tail.extend_from_slice(b"extra");
            let result = decode(&with_tail).unwrap();
            assert_eq!(result.key.unwrap().to_string(), *expected, "{raw:?}");
            assert_eq!(result.consumed, raw.len());
            for end in 0..raw.len() {
                assert_eq!(
                    decode(&raw[..end]),
                    Err(DecodeError::Incomplete),
                    "{raw:?} at {end}"
                );
            }
        }
    }

    #[test]
    fn reject_unknown_or_malformed_input() {
        for raw in [
            b"\x1b[200~paste".as_slice(),
            b"\x1b[999~",
            b"\xff",
            b"\xc0\xaf",
            b"\xed\xa0\x80",
            b"\x1b[1;9A",
            b"\x1b[1;5;2A",
            b"\x1b[9999999999999999999u",
            b"\x1b[55296u",
            b"\x1b[<;1;2M",
            b"\x1b[<1;2;3X",
            b"\x1bOX",
        ] {
            assert_eq!(decode(raw), Err(DecodeError::Unsupported), "{raw:?}");
        }
        assert_eq!(decode(&[27; 1000]), Err(DecodeError::Unsupported));
    }

    #[test]
    fn ignore_mouse_reports() {
        for raw in [
            b"\x1b[<0;12;4M".as_slice(),
            b"\x1b[<0;12;4m",
            b"\x1b[<64;12;4M",
        ] {
            assert_eq!(
                decode(raw).unwrap(),
                Decoded {
                    key: None,
                    consumed: raw.len()
                }
            );
            for end in 0..raw.len() {
                assert_eq!(decode(&raw[..end]), Err(DecodeError::Incomplete));
            }
        }
    }

    #[test]
    fn escape_deadline() {
        for (raw, name) in [
            (b"\x1b".as_slice(), "Escape"),
            (b"\x1b[", "M-["),
            (b"\x1bO", "M-O"),
        ] {
            assert_eq!(finish(raw).unwrap().unwrap().to_string(), name);
        }
        assert_eq!(finish(b"\xc3"), Err(DecodeError::Unsupported));
    }
}
