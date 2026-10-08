fn main() {
    use std::{
        env,
        process::Command,
        time::{SystemTime, UNIX_EPOCH},
    };
    println!("cargo:rerun-if-env-changed=RAGAGENT_SOURCE_COMMIT");
    println!("cargo:rerun-if-env-changed=RAGAGENT_BUILD_TIME");
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/refs");
    let valid = |value: &str| {
        value.len() == 40
            && value
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    };
    let commit = env::var("RAGAGENT_SOURCE_COMMIT")
        .ok()
        .filter(|s| valid(s))
        .or_else(|| {
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .output()
                .ok()
                .filter(|o| o.status.success())
                .and_then(|o| String::from_utf8(o.stdout).ok())
                .map(|s| s.trim().to_owned())
                .filter(|s| valid(s))
        })
        .unwrap_or_else(|| "unknown".into());
    let built = env::var("RAGAGENT_BUILD_TIME")
        .ok()
        .filter(|s| {
            s.len() <= 40
                && s.bytes()
                    .all(|c| c.is_ascii_digit() || b"TZ:+. -".contains(&c))
        })
        .unwrap_or_else(|| {
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
                .to_string()
        });
    println!("cargo:rustc-env=RAGAGENT_SOURCE_COMMIT={commit}");
    println!("cargo:rustc-env=RAGAGENT_BUILD_TIME={built}");
    tauri_build::build()
}
