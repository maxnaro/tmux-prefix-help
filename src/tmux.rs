use crate::terminal::Input;
use std::env;
use std::ffi::{OsStr, OsString};
use std::io;
use std::path::Path;
use std::process::{Command, Stdio};

pub struct Tmux<'a> {
    socket: Option<&'a OsStr>,
}

pub fn output(command: &mut Command) -> io::Result<String> {
    let result = command
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()?;
    if !result.status.success() {
        return Err(io::Error::other("tmux command failed"));
    }
    String::from_utf8(result.stdout)
        .map(|text| text.trim_end_matches(['\r', '\n']).to_owned())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

pub fn execute(command: &mut Command) -> io::Result<()> {
    if command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .status()?
        .success()
    {
        Ok(())
    } else {
        Err(io::Error::other("tmux command failed"))
    }
}

impl<'a> Tmux<'a> {
    pub fn at(socket: &'a OsStr) -> Self {
        Self {
            socket: Some(socket),
        }
    }

    pub fn command(&self) -> Command {
        let mut command = Command::new("tmux");
        if let Some(socket) = self.socket {
            command.arg("-S").arg(socket);
        }
        command
    }

    pub fn display(&self, client: &OsStr, format: &str) -> io::Result<String> {
        output(
            self.command()
                .args(["display-message", "-p", "-c"])
                .arg(client)
                .arg(format),
        )
    }

    pub fn in_session(&self, client: &OsStr, session: &OsStr) -> bool {
        self.display(client, "#{session_id}")
            .is_ok_and(|current| OsStr::new(&current) == session)
    }

    pub fn waiting(&self, client: &OsStr, session: &OsStr) -> bool {
        let mut expected = session.to_os_string();
        expected.push(":1");
        self.display(client, "#{session_id}:#{client_prefix}")
            .is_ok_and(|current| OsStr::new(&current) == expected)
    }
}

pub fn shell_quote(value: &OsStr) -> io::Result<String> {
    let text = value.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "tmux shell command paths must be UTF-8",
        )
    })?;
    Ok(format!("'{}'", text.replace('\'', "'\\''")))
}

pub fn load(plugin_dir: &Path) -> io::Result<()> {
    let tmux = Tmux { socket: None };
    if output(
        tmux.command()
            .args(["show-options", "-gqv", "@prefix-help-delay"]),
    )?
    .is_empty()
    {
        execute(
            tmux.command()
                .args(["set-option", "-g", "@prefix-help-delay", "500"]),
        )?;
    }
    execute(
        tmux.command()
            .args(["set-option", "-g", "status-interval", "1"]),
    )?;
    let executable = shell_quote(env::current_exe()?.as_os_str())?;
    let arguments = "#{q:socket_path} #{q:session_id} #{pid} #{q:client_name}";
    let trigger = format!("#{{?client_prefix,#({executable} watch {arguments}),}}");
    let previous = output(
        tmux.command()
            .args(["show-options", "-gqv", "status-right"]),
    )?;
    let legacy_path = shell_quote(plugin_dir.join("scripts/prefix-help.sh").as_os_str())?;
    let legacy = format!("#{{?client_prefix,#(bash {legacy_path} {arguments}),}}");
    let mut updated = previous.replace(&legacy, "");
    if !updated.contains(&trigger) {
        updated.push_str(&trigger);
    }
    if updated != previous {
        execute(
            tmux.command()
                .args(["set-option", "-g", "status-right"])
                .arg(updated),
        )?;
    }
    Ok(())
}

pub fn replay_args(socket: &OsStr, client: &OsStr, input: Input) -> Vec<OsString> {
    let (mut keys, error) = match input {
        Input::Keys(keys) => (keys, false),
        Input::Unsupported => (Vec::new(), true),
    };
    let cancelled = keys.first().is_none_or(|key| key.is_escape());
    if cancelled && !keys.is_empty() {
        keys.remove(0);
    }
    let mut args: Vec<OsString> = [
        OsStr::new("-S"),
        socket,
        OsStr::new("display-popup"),
        OsStr::new("-C"),
        OsStr::new("-c"),
        client,
        OsStr::new(";"),
        OsStr::new("switch-client"),
        OsStr::new("-c"),
        client,
        OsStr::new("-T"),
        OsStr::new(if cancelled { "root" } else { "prefix" }),
    ]
    .into_iter()
    .map(OsStr::to_os_string)
    .collect();
    if !keys.is_empty() {
        args.extend(
            [
                OsStr::new(";"),
                OsStr::new("send-keys"),
                OsStr::new("-K"),
                OsStr::new("-c"),
                client,
                OsStr::new("--"),
            ]
            .into_iter()
            .map(OsStr::to_os_string),
        );
        args.extend(keys.into_iter().map(|key| {
            let text = key.to_string();
            // tmux interprets a trailing semicolon even in a direct argv call.
            OsString::from(
                text.strip_suffix(';')
                    .map_or_else(|| text.clone(), |prefix| format!("{prefix}\\;")),
            )
        }));
    }
    if error {
        args.extend(
            [
                OsStr::new(";"),
                OsStr::new("display-message"),
                OsStr::new("-c"),
                client,
                OsStr::new("Prefix help: unsupported terminal input; press prefix again"),
            ]
            .into_iter()
            .map(OsStr::to_os_string),
        );
    }
    args
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::input::{Key, decode};

    #[test]
    fn close_before_replay_and_escape_semicolons() {
        let keys = vec![
            Key::character(u32::from(';')).unwrap(),
            decode(b"\x1b;").unwrap().key.unwrap(),
        ];
        let args = replay_args(
            OsStr::new("/socket"),
            OsStr::new("client"),
            Input::Keys(keys),
        );
        assert!(
            args.iter().position(|arg| arg == "display-popup").unwrap()
                < args.iter().position(|arg| arg == "send-keys").unwrap()
        );
        assert_eq!(&args[args.len() - 2..], ["\\;", "M-\\;"]);
    }

    #[test]
    fn escape_consumed_before_root_typing() {
        let args = replay_args(
            OsStr::new("/socket"),
            OsStr::new("client"),
            Input::Keys(vec![Key::named("Escape"), Key::character(120).unwrap()]),
        );
        assert!(args.iter().any(|arg| arg == "root"));
        assert_eq!(args.last().unwrap(), "x");
    }

    #[test]
    fn unsupported_input_has_no_replay() {
        let args = replay_args(
            OsStr::new("/socket"),
            OsStr::new("client"),
            Input::Unsupported,
        );
        assert!(args.iter().any(|arg| arg == "root"));
        assert!(args.iter().any(|arg| arg == "display-message"));
        assert!(!args.iter().any(|arg| arg == "send-keys"));
    }
}
