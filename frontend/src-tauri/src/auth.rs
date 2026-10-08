//! Native-only bearer generation/storage. IPC returns only a nonsecret verifier.
use keyring::{Entry, Error};
use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialStatus {
    pub available: bool,
    pub token_hash: Option<String>,
    pub error_code: Option<String>,
}

fn generate() -> String {
    // UUID v4 uses the OS cryptographic RNG; two UUIDs provide 244 random bits.
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}
fn valid(value: &str) -> bool {
    (43..=128).contains(&value.len())
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
}
fn verifier(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}
fn credential() -> Result<String, &'static str> {
    let entry = Entry::new("org.scientific-ragagent.desktop", "local-api-v1")
        .map_err(|_| "local_secure_storage_unavailable")?;
    let value = match entry.get_password() {
        Ok(value) => value,
        Err(Error::NoEntry) => {
            let value = generate();
            entry
                .set_password(&value)
                .map_err(|_| "local_secure_storage_unavailable")?;
            value
        }
        Err(_) => return Err("local_secure_storage_unavailable"),
    };
    if !valid(&value) {
        return Err("local_auth_credential_invalid");
    }
    Ok(value)
}

pub fn initialize() -> Result<(reqwest::Client, CredentialStatus), String> {
    match credential() {
        Ok(value) => Ok((
            crate::bridge::client_with_token(Some(&value))?,
            CredentialStatus {
                available: true,
                token_hash: Some(verifier(&value)),
                error_code: None,
            },
        )),
        Err(code) => Ok((
            crate::bridge::client()?,
            CredentialStatus {
                available: false,
                token_hash: None,
                error_code: Some(code.into()),
            },
        )), // Fail closed: privileged backend routes reject this uncredentialed client.
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_credential_is_private_and_only_hash_metadata_is_serialized() {
        let first = generate();
        let second = generate();
        assert!(first != second && valid(&first) && valid(&second));
        assert!(!valid("") && !valid("plaintext\nheader"));
        let status = CredentialStatus {
            available: true,
            token_hash: Some(verifier(&first)),
            error_code: None,
        };
        let serialized = serde_json::to_string(&status).unwrap();
        assert!(!serialized.contains(&first));
        assert_eq!(status.token_hash.unwrap().len(), 64);
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_credential_manager_round_trip_and_cleanup() {
        let service = format!("org.scientific-ragagent.test.{}", Uuid::new_v4());
        let entry = Entry::new(&service, "isolated-test").expect("credential_store_unavailable");
        let value = generate();
        entry.set_password(&value).expect("credential_write_failed");
        let recovered = entry.get_password().expect("credential_read_failed");
        entry.delete_credential().expect("credential_delete_failed");
        assert!(recovered == value);
        assert!(matches!(entry.get_password(), Err(Error::NoEntry)));
    }
}
