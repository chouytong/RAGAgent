//! The renderer has no network capability. Every request ends at this one local API.
use base64::{engine::general_purpose::STANDARD, Engine};
use futures_util::StreamExt;
use reqwest::{redirect::Policy, Client, Method, Response};
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, sync::Mutex, time::Duration};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

pub const API_BASE: &str = "http://127.0.0.1:8000";
const MAX_REQUEST_BYTES: usize = 128 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 32 * 1024 * 1024;
const MAX_EVENT_BYTES: usize = 2 * 1024 * 1024;
const MAX_PENDING: usize = 24;

#[derive(Default)]
struct PendingState {
    active: HashMap<String, CancellationToken>,
    before_start: HashMap<String, std::time::Instant>,
    finished: HashMap<String, std::time::Instant>,
}
const CANCEL_METADATA_LIMIT: usize = 128;
const CANCEL_METADATA_TTL: Duration = Duration::from_secs(30);
fn prune_metadata(items: &mut HashMap<String, std::time::Instant>) {
    items.retain(|_, created| created.elapsed() < CANCEL_METADATA_TTL);
    while items.len() >= CANCEL_METADATA_LIMIT {
        let Some(oldest) = items
            .iter()
            .min_by_key(|(_, created)| **created)
            .map(|(id, _)| id.clone())
        else {
            break;
        };
        items.remove(&oldest);
    }
}
#[derive(Default)]
pub struct Pending(Mutex<PendingState>);
impl Pending {
    pub fn register(&self, id: &str) -> Result<CancellationToken, String> {
        if Uuid::parse_str(id).is_err() {
            return Err("invalid_request_id".into());
        }
        let mut pending = self.0.lock().map_err(|_| "bridge_unavailable")?;
        prune_metadata(&mut pending.before_start);
        prune_metadata(&mut pending.finished);
        if pending.active.contains_key(id) || pending.finished.contains_key(id) {
            return Err("request_id_reused".into());
        }
        if pending.active.len() >= MAX_PENDING {
            return Err("bridge_busy".into());
        }
        let token = CancellationToken::new();
        if pending.before_start.remove(id).is_some() {
            token.cancel();
        }
        pending.active.insert(id.into(), token.clone());
        Ok(token)
    }
    pub fn cancel(&self, id: &str) {
        if Uuid::parse_str(id).is_err() {
            return;
        }
        if let Ok(mut pending) = self.0.lock() {
            prune_metadata(&mut pending.before_start);
            prune_metadata(&mut pending.finished);
            if let Some(token) = pending.active.get(id) {
                token.cancel();
            } else if !pending.finished.contains_key(id) {
                pending
                    .before_start
                    .insert(id.into(), std::time::Instant::now());
            }
        }
    }
    pub fn finish(&self, id: &str) {
        if let Ok(mut pending) = self.0.lock() {
            pending.active.remove(id);
            pending.before_start.remove(id);
            prune_metadata(&mut pending.finished);
            pending
                .finished
                .insert(id.into(), std::time::Instant::now());
        }
    }
    pub fn cancel_all(&self) {
        if let Ok(pending) = self.0.lock() {
            for token in pending.active.values() {
                token.cancel();
            }
        }
    }
}

pub fn client() -> Result<Client, String> {
    Client::builder()
        .no_proxy()
        .redirect(Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .read_timeout(Duration::from_secs(35))
        .build()
        .map_err(|_| "bridge_unavailable".into())
}

fn uuid(value: &str) -> bool {
    value.len() == 36 && Uuid::parse_str(value).is_ok()
}
fn api_path(path: &str) -> Result<(&str, Option<&str>), String> {
    if path.len() > 4096
        || !path.is_ascii()
        || path.contains(['\\', '#', '%'])
        || path.chars().any(char::is_whitespace)
    {
        return Err("invalid_local_path".into());
    }
    let (route, query) = path
        .split_once('?')
        .map_or((path, None), |(route, query)| (route, Some(query)));
    if !route.starts_with("/api/")
        || route.contains("//")
        || route.split('/').any(|s| s == "." || s == "..")
    {
        return Err("invalid_local_path".into());
    }
    if let Some(query) = query {
        // Only pagination/cursors and the explicit workflow mode are accepted.
        if query.is_empty()
            || query.split('&').any(|entry| {
                let Some((key, value)) = entry.split_once('=') else {
                    return true;
                };
                !((matches!(key, "after" | "limit" | "offset")
                    && !value.is_empty()
                    && value.bytes().all(|b| b.is_ascii_digit()))
                    || (key == "mode" && matches!(value, "rag" | "research")))
            })
        {
            return Err("invalid_local_query".into());
        }
    }
    Ok((route, query))
}

pub fn validate_request(path: &str, method: &str) -> Result<(), String> {
    let (route, query) = api_path(path)?;
    if query.is_some() && method != "GET" {
        return Err("invalid_local_query".into());
    }
    let parts: Vec<&str> = route.trim_start_matches('/').split('/').collect();
    let allowed = match parts.as_slice() {
        ["api", "health" | "ready"] => method == "GET",
        ["api", "search"] | ["api", "rag", "query"] | ["api", "research"] => method == "POST",
        ["api", "providers"] => matches!(method, "GET" | "PUT"),
        ["api", "providers", "test"] => method == "POST",
        ["api", "papers"] => method == "GET",
        ["api", "papers", "upload" | "arxiv"] => method == "POST",
        ["api", "papers", id] if uuid(id) => matches!(method, "GET" | "PATCH"),
        ["api", "papers", id, "chunks"] if uuid(id) => method == "GET",
        ["api", "papers", id, "retry"] if uuid(id) => method == "POST",
        ["api", "papers", id, "chunks", chunk, "entities"] if uuid(id) && uuid(chunk) => {
            method == "POST"
        }
        ["api", "runs" | "rag" | "research", id] if uuid(id) => method == "GET",
        ["api", "runs", id, "cancel"] if uuid(id) => method == "POST",
        ["api", "evaluations", "retrieval" | "rag" | "multi-agent" | "conversation"] => {
            method == "POST"
        }
        ["api", "conversations"] => matches!(method, "GET" | "POST"),
        ["api", "conversations", id] if uuid(id) => matches!(method, "GET" | "PATCH" | "DELETE"),
        ["api", "conversations", id, "messages"] if uuid(id) => matches!(method, "GET" | "POST"),
        ["api", "conversations", id, "memory"] if uuid(id) => method == "DELETE",
        ["api", "conversations", id, "summary"] if uuid(id) => matches!(method, "GET" | "DELETE"),
        ["api", "conversations", id, "memories"] if uuid(id) => matches!(method, "GET" | "POST"),
        ["api", "conversations", id, "memories", item] if uuid(id) && uuid(item) => {
            method == "DELETE"
        }
        ["api", "conversations", id, "clear"] if uuid(id) => method == "POST",
        ["api", "conversations", id, "messages", message, "retry"] if uuid(id) && uuid(message) => {
            method == "POST"
        }
        _ => false,
    };
    if allowed {
        Ok(())
    } else {
        Err("local_route_not_allowed".into())
    }
}

pub fn validate_stream(path: &str) -> Result<(), String> {
    let (route, query) = api_path(path)?;
    let parts: Vec<&str> = route.trim_start_matches('/').split('/').collect();
    if query.is_none()
        && matches!(parts.as_slice(), ["api", "runs"|"rag"|"research", id, "events"] if uuid(id))
    {
        Ok(())
    } else {
        Err("local_stream_not_allowed".into())
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApiRequest {
    pub id: String,
    pub path: String,
    pub method: String,
    pub content_type: Option<String>,
    pub body: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApiResponse {
    pub status: u16,
    pub content_type: Option<String>,
    pub body: String,
}

pub async fn limited_bytes(response: Response, limit: usize) -> Result<Vec<u8>, String> {
    if response
        .content_length()
        .is_some_and(|length| length > limit as u64)
    {
        return Err("local_response_too_large".into());
    }
    let mut data = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(part) = stream.next().await {
        let part = part.map_err(|_| "local_backend_unavailable")?;
        if data.len().saturating_add(part.len()) > limit {
            return Err("local_response_too_large".into());
        }
        data.extend_from_slice(&part);
    }
    Ok(data)
}

pub async fn request(client: &Client, request: &ApiRequest) -> Result<ApiResponse, String> {
    validate_request(&request.path, &request.method)?;
    let method =
        Method::from_bytes(request.method.as_bytes()).map_err(|_| "invalid_local_method")?;
    let mut builder = client
        .request(method, format!("{API_BASE}{}", request.path))
        .timeout(Duration::from_secs(60));
    if let Some(encoded) = &request.body {
        if encoded.len() > MAX_REQUEST_BYTES / 3 * 4 + 4 {
            return Err("local_request_too_large".into());
        }
        let body = STANDARD.decode(encoded).map_err(|_| "invalid_local_body")?;
        if body.len() > MAX_REQUEST_BYTES {
            return Err("local_request_too_large".into());
        }
        let content_type = request
            .content_type
            .as_deref()
            .ok_or("invalid_local_content_type")?;
        let multipart = content_type.strip_prefix("multipart/form-data; boundary=");
        let valid_type = content_type == "application/json"
            || multipart.is_some_and(|v| {
                !v.is_empty()
                    && v.len() <= 80
                    && v.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"'-_".contains(&b))
            });
        if !valid_type {
            return Err("invalid_local_content_type".into());
        }
        builder = builder.header("Content-Type", content_type).body(body);
    }
    let response = builder
        .send()
        .await
        .map_err(|_| "local_backend_unavailable")?;
    if response.status().is_redirection() {
        return Err("local_redirect_forbidden".into());
    }
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get("Content-Type")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    let body = STANDARD.encode(limited_bytes(response, MAX_RESPONSE_BYTES).await?);
    Ok(ApiResponse {
        status,
        content_type,
        body,
    })
}

#[derive(Serialize)]
#[serde(tag = "event", rename_all = "lowercase")]
pub enum StreamEvent {
    Execution { data: String, id: String },
    Done { data: String },
    Error { data: String },
}
#[derive(Default)]
pub struct SseParser {
    buffer: Vec<u8>,
    event: String,
    data: Vec<String>,
    id: Option<u64>,
    event_bytes: usize,
}
impl SseParser {
    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<StreamEvent>, String> {
        self.buffer.extend_from_slice(bytes);
        let mut events = Vec::new();
        while let Some(end) = self.buffer.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buffer.drain(..=end).collect();
            self.event_bytes += line.len();
            if self.event_bytes > MAX_EVENT_BYTES {
                return Err("local_event_too_large".into());
            }
            let line = std::str::from_utf8(&line[..line.len() - 1])
                .map_err(|_| "invalid_local_event")?
                .trim_end_matches('\r');
            if line.is_empty() {
                let data = self.data.join("\n");
                if self.event == "execution" && !data.is_empty() {
                    let id = self.id.ok_or("invalid_local_event_cursor")?;
                    events.push(StreamEvent::Execution {
                        data,
                        id: id.to_string(),
                    });
                } else if self.event == "done" && !data.is_empty() {
                    events.push(StreamEvent::Done { data });
                }
                self.event.clear();
                self.data.clear();
                self.id = None;
                self.event_bytes = 0;
            } else if !line.starts_with(':') {
                let (field, value) = line.split_once(':').unwrap_or((line, ""));
                let value = value.strip_prefix(' ').unwrap_or(value);
                match field {
                    "event" => self.event = value.into(),
                    "data" => self.data.push(value.into()),
                    "id" => {
                        self.id = Some(value.parse().map_err(|_| "invalid_local_event_cursor")?)
                    }
                    _ => {}
                }
            }
        }
        if self.buffer.len().saturating_add(self.event_bytes) > MAX_EVENT_BYTES {
            return Err("local_event_too_large".into());
        }
        Ok(events)
    }
}

pub async fn events<F: Fn(StreamEvent) -> bool>(
    client: &Client,
    path: &str,
    after: u64,
    cancel: &CancellationToken,
    send: F,
) -> Result<(), String> {
    validate_stream(path)?;
    let mut cursor = after;
    loop {
        let attempt = async {
            let response = client
                .get(format!("{API_BASE}{path}?after={cursor}"))
                .header("Accept", "text/event-stream")
                .send()
                .await
                .map_err(|_| "local_backend_unavailable")?;
            if !response.status().is_success()
                || !response
                    .headers()
                    .get("Content-Type")
                    .and_then(|v| v.to_str().ok())
                    .is_some_and(|v| v.starts_with("text/event-stream"))
            {
                return Err("local_stream_unavailable".into());
            }
            let mut stream = response.bytes_stream();
            let mut parser = SseParser::default();
            while let Some(part) = stream.next().await {
                let bytes = part.map_err(|_| "local_stream_unavailable")?;
                for event in parser.push(&bytes)? {
                    if let StreamEvent::Execution { id, .. } = &event {
                        cursor = id.parse().map_err(|_| "invalid_local_event_cursor")?;
                    }
                    let done = matches!(event, StreamEvent::Done { .. });
                    if !send(event) {
                        return Ok(true);
                    }
                    if done {
                        return Ok(true);
                    }
                }
            }
            Err::<bool, String>("local_stream_disconnected".into())
        };
        let result = tokio::select! {_ = cancel.cancelled()=>return Ok(()), result=attempt=>result};
        match result {
            Ok(true) => return Ok(()),
            Ok(false) => {}
            Err(code) => {
                if !send(StreamEvent::Error { data: code }) {
                    return Ok(());
                }
            }
        }
        tokio::select! {_ = cancel.cancelled()=>return Ok(()), _=tokio::time::sleep(Duration::from_secs(2))=>{}}
    }
}

#[derive(Debug, PartialEq)]
pub struct Resource {
    pub path: String,
    pub suffix: &'static str,
    pub page: Option<u32>,
}
pub fn resource(path: &str) -> Result<Resource, String> {
    let (route, fragment) = path
        .split_once('#')
        .map_or((path, None), |(a, b)| (a, Some(b)));
    let (route, query) = api_path(route)?;
    if query.is_some() {
        return Err("local_resource_not_allowed".into());
    }
    let parts: Vec<&str> = route.trim_start_matches('/').split('/').collect();
    let suffix = match parts.as_slice() {
        ["api", "papers", id, "pdf"] if uuid(id) => "pdf",
        ["api", "evaluations", id, "results.json"] if uuid(id) => "json",
        ["api", "evaluations", id, "results.md"] if uuid(id) => "md",
        _ => return Err("local_resource_not_allowed".into()),
    };
    let page = if let Some(fragment) = fragment {
        if suffix != "pdf" {
            return Err("invalid_local_page".into());
        }
        let page: u32 = fragment
            .strip_prefix("page=")
            .ok_or("invalid_local_page")?
            .parse()
            .map_err(|_| "invalid_local_page")?;
        if page == 0 || page > 100_000 {
            return Err("invalid_local_page".into());
        }
        Some(page)
    } else {
        None
    };
    Ok(Resource {
        path: route.into(),
        suffix,
        page,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    const ID: &str = "12345678-1234-1234-1234-123456789abc";
    #[test]
    fn requests_are_local_and_scoped() {
        assert!(validate_request("/api/health", "GET").is_ok());
        assert!(validate_request(&format!("/api/conversations/{ID}/messages"), "POST").is_ok());
        assert!(validate_request("/api/providers", "PUT").is_ok());
        for path in [
            "https://evil.example/api/health",
            "//evil/api/health",
            "/api/../health",
            "/api/%2e%2e/health",
            "/api/health?next=http://evil",
            "/api/health#x",
            "/api\\health",
            "/api/health\r\nHost:evil",
        ] {
            assert!(validate_request(path, "GET").is_err(), "{path}");
        }
        assert!(validate_request("/api/health", "POST").is_err());
        assert!(validate_request(&format!("/api/runs/{ID}/events"), "GET").is_err());
        assert!(validate_request("/api/conversations?limit=20&offset=0", "GET").is_ok());
        assert!(validate_request("/api/conversations?mode=research", "GET").is_ok());
    }
    #[test]
    fn documents_do_not_allow_arbitrary_destinations() {
        assert_eq!(
            resource(&format!("/api/papers/{ID}/pdf#page=3"))
                .unwrap()
                .page,
            Some(3)
        );
        assert!(resource(&format!("/api/evaluations/{ID}/results.md")).is_ok());
        for path in [
            format!("/api/papers/{ID}/pdf#page=0"),
            format!("/api/papers/{ID}/pdf?url=http://evil"),
            format!("/api/evaluations/{ID}/secret.env"),
            "file:///etc/passwd".into(),
            "https://evil/paper.pdf".into(),
        ] {
            assert!(resource(&path).is_err());
        }
    }
    #[test]
    fn sse_reassembles_utf8_multiline_and_cursor() {
        let source="id: 7\r\nevent: execution\r\ndata: 科研\r\ndata: context\r\n\r\nevent: done\ndata: {}\n\n";
        let mut parser = SseParser::default();
        let mut events = Vec::new();
        for byte in source.as_bytes() {
            events.extend(parser.push(&[*byte]).unwrap());
        }
        assert_eq!(events.len(), 2);
        assert!(
            matches!(&events[0],StreamEvent::Execution{data,id} if data=="科研\ncontext" && id=="7")
        );
        assert!(matches!(&events[1],StreamEvent::Done{data} if data=="{}"));
    }
    #[test]
    fn sse_rejects_invalid_cursor_and_oversize() {
        assert!(SseParser::default()
            .push(b"id: bad\nevent: execution\ndata: {}\n\n")
            .is_err());
        assert!(SseParser::default()
            .push(&vec![b'x'; MAX_EVENT_BYTES + 1])
            .is_err());
    }
    #[test]
    fn late_cancellation_never_uses_active_request_slots() {
        let pending = Pending::default();
        for _ in 0..MAX_PENDING * 4 {
            let id = Uuid::new_v4().to_string();
            let token = pending.register(&id).unwrap();
            pending.finish(&id);
            pending.cancel(&id);
            assert!(!token.is_cancelled());
        }
        for _ in 0..MAX_PENDING {
            pending.register(&Uuid::new_v4().to_string()).unwrap();
        }
        assert_eq!(pending.0.lock().unwrap().active.len(), MAX_PENDING);
    }
    #[test]
    fn orphan_cancellation_is_bounded_expiring_and_separate() {
        let pending = Pending::default();
        for _ in 0..CANCEL_METADATA_LIMIT * 4 {
            pending.cancel(&Uuid::new_v4().to_string());
        }
        assert!(pending.0.lock().unwrap().before_start.len() <= CANCEL_METADATA_LIMIT);
        let id = Uuid::new_v4().to_string();
        pending.cancel(&id);
        pending.0.lock().unwrap().before_start.insert(
            id.clone(),
            std::time::Instant::now() - CANCEL_METADATA_TTL - Duration::from_secs(1),
        );
        assert!(!pending.register(&id).unwrap().is_cancelled());
        pending.finish(&id);
        assert!(pending.register(&id).is_err());
    }
    #[test]
    fn subscriptions_are_bounded_and_cancelled() {
        let pending = Pending::default();
        let id = Uuid::new_v4().to_string();
        pending.cancel(&id);
        assert!(pending.register(&id).unwrap().is_cancelled());
        pending.finish(&id);
        for _ in 0..MAX_PENDING {
            pending.register(&Uuid::new_v4().to_string()).unwrap();
        }
        assert!(pending.register(&Uuid::new_v4().to_string()).is_err());
        pending.cancel_all();
        assert!(pending
            .0
            .lock()
            .unwrap()
            .active
            .values()
            .all(CancellationToken::is_cancelled));
    }
}
