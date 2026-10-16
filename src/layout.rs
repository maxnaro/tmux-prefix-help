struct Group {
    title: &'static str,
    items: &'static [(&'static str, &'static str)],
}

const GROUPS: [Group; 4] = [
    Group {
        title: "WINDOWS",
        items: &[
            ("c", "New window"),
            ("n / p", "Next / previous"),
            ("1-9", "Select window"),
            ("w", "Choose window"),
            (",", "Rename window"),
        ],
    },
    Group {
        title: "PANES",
        items: &[
            ("%", "Split left / right"),
            ("\"", "Split top / bottom"),
            ("arrows", "Focus pane"),
            ("z", "Zoom / unzoom"),
            ("x", "Close pane"),
        ],
    },
    Group {
        title: "COPY & SESSIONS",
        items: &[
            ("[", "Copy / scrollback"),
            ("]", "Paste buffer"),
            ("s", "Choose session"),
            ("d", "Detach"),
        ],
    },
    Group {
        title: "TOOLS",
        items: &[
            ("r", "Refresh client"),
            (":", "Command prompt"),
            ("?", "All keybindings"),
            ("Esc", "Dismiss help"),
        ],
    },
];

const CYAN: &str = "136;192;208";
const MUTED: &str = "146;158;178";

fn styled(text: &str, color: &str, bold: bool) -> String {
    format!(
        "\x1b[38;2;{color}m{}{text}\x1b[39;22m",
        if bold { "\x1b[1m" } else { "" }
    )
}

fn cell(text: &str, width: usize) -> String {
    // The fixed hints are ASCII, so byte counts equal terminal columns.
    format!("{:<width$}", &text[..text.len().min(width)])
}

fn heading(group: &Group, width: usize) -> String {
    styled(&cell(group.title, width), MUTED, true)
}

fn item((key, label): (&str, &str), width: usize) -> String {
    let key_width = width.min(8);
    styled(&cell(key, key_width), CYAN, true) + &cell(label, width - key_width)
}

pub fn hint_lines(columns: usize, rows: usize) -> Vec<String> {
    if rows == 0 {
        return Vec::new();
    }
    let width = columns.saturating_sub(1);
    let margin = if width >= 30 { 2 } else { 0 };
    let usable = width.saturating_sub(margin * 2);
    let padding = " ".repeat(margin);
    let mut lines = Vec::new();
    if usable >= 62 {
        let cell_width = (usable - 4) / 2;
        for [left, right] in GROUPS.as_chunks::<2>().0 {
            lines.push(format!(
                "{padding}{}    {}",
                heading(left, cell_width),
                heading(right, cell_width)
            ));
            for (a, b) in left.items.iter().zip(right.items) {
                lines.push(format!(
                    "{padding}{}    {}",
                    item(*a, cell_width),
                    item(*b, cell_width)
                ));
            }
            lines.push(String::new());
        }
    } else {
        for group in &GROUPS {
            lines.push(format!("{padding}{}", heading(group, usable)));
            for pair in group.items {
                lines.push(format!("{padding}{}", item(*pair, usable)));
            }
            lines.push(String::new());
        }
    }
    let footer = [
        "Any key works  /  ? all bindings  /  Esc to dismiss",
        "? All bindings  /  Esc to dismiss",
        "Esc to dismiss",
    ]
    .into_iter()
    .find(|text| text.len() <= usable)
    .unwrap_or("Esc to dismiss");
    lines.truncate(rows - 1);
    lines.resize(rows - 1, String::new());
    lines.push(format!(
        "{padding}{}",
        styled(&footer[..footer.len().min(usable)], MUTED, false)
    ));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(text: &str) -> String {
        let mut output = String::new();
        let mut escape = false;
        for ch in text.chars() {
            if ch == '\x1b' {
                escape = true;
            } else if escape && ch == 'm' {
                escape = false;
            } else if !escape {
                output.push(ch);
            }
        }
        output
    }

    #[test]
    fn full_layout() {
        let lines = hint_lines(68, 14);
        let text = plain(&lines.join("\n"));
        for label in [
            "WINDOWS",
            "PANES",
            "COPY & SESSIONS",
            "TOOLS",
            "1-9",
            "arrows",
            "All keybindings",
            "Esc to dismiss",
        ] {
            assert!(text.contains(label), "{label}");
        }
        assert!(lines.join("").contains("\x1b[1m"));
    }

    #[test]
    fn layouts_fit_even_degenerate_sizes() {
        for columns in 0..100 {
            for rows in 0..30 {
                let lines = hint_lines(columns, rows);
                assert_eq!(lines.len(), rows);
                for line in &lines {
                    assert!(
                        plain(line).len() <= columns.saturating_sub(1),
                        "{columns}x{rows}: {line}"
                    );
                }
                if columns >= 15 && rows > 0 {
                    assert!(plain(lines.last().unwrap()).contains("dismiss"));
                }
            }
        }
    }
}
