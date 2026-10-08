#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod auth;
mod bridge;
use base64::{engine::general_purpose::STANDARD, Engine};
use bridge::{ApiRequest, ApiResponse, Pending, StreamEvent, API_BASE};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    sync::Arc,
};
use tauri::{ipc::Channel, Manager, State, WebviewUrl, WebviewWindowBuilder};
use uuid::Uuid;

struct Backend {
    client: reqwest::Client,
    pending: Arc<Pending>,
    document_lock: tokio::sync::Mutex<()>,
    credentials: auth::CredentialStatus,
}

#[tauri::command]
fn local_auth_status(state: State<'_, Backend>) -> auth::CredentialStatus {
    state.credentials.clone()
}

#[tauri::command]
fn desktop_build_info() -> serde_json::Value {
    serde_json::json!({"version": env!("CARGO_PKG_VERSION"), "sourceCommit": env!("RAGAGENT_SOURCE_COMMIT"), "sourceDirty": env!("RAGAGENT_SOURCE_DIRTY"), "builtAtUtc": env!("RAGAGENT_BUILD_TIME"), "platform": std::env::consts::OS, "architecture": std::env::consts::ARCH, "signing": "unsigned"})
}

#[tauri::command]
async fn api_request(
    request: ApiRequest,
    state: State<'_, Backend>,
) -> Result<ApiResponse, String> {
    let token = state.pending.register(&request.id)?;
    let result = tokio::select! { _=token.cancelled()=>Err("local_request_cancelled".into()), result=bridge::request(&state.client,&request)=>result };
    state.pending.finish(&request.id);
    result
}
#[tauri::command]
fn cancel_request(id: String, state: State<'_, Backend>) {
    state.pending.cancel(&id);
}
#[tauri::command]
async fn run_events(
    id: String,
    path: String,
    after: u64,
    on_event: Channel<StreamEvent>,
    state: State<'_, Backend>,
) -> Result<(), String> {
    bridge::validate_stream(&path)?;
    let token = state.pending.register(&id)?;
    let result = bridge::events(&state.client, &path, after, &token, |event| {
        on_event.send(event).is_ok()
    })
    .await;
    state.pending.finish(&id);
    result
}
#[tauri::command]
async fn open_resource(
    path: String,
    app: tauri::AppHandle,
    state: State<'_, Backend>,
) -> Result<(), String> {
    let resource = bridge::resource(&path)?;
    let _guard = state.document_lock.lock().await;
    let response = state
        .client
        .get(format!("{API_BASE}{}", resource.path))
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
        .map_err(|_| "local_backend_unavailable")?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err("local_auth_required".into());
    }
    if !response.status().is_success() {
        return Err("local_document_unavailable".into());
    }
    let bytes = bridge::limited_bytes(response, 128 * 1024 * 1024).await?;
    if resource.suffix == "pdf" && !bytes.starts_with(b"%PDF-") {
        return Err("invalid_local_pdf".into());
    }
    if resource.suffix == "json" && serde_json::from_slice::<serde_json::Value>(&bytes).is_err() {
        return Err("invalid_local_artifact".into());
    }
    if resource.suffix == "md" && std::str::from_utf8(&bytes).is_err() {
        return Err("invalid_local_artifact".into());
    }
    let directory = app
        .path()
        .app_cache_dir()
        .map_err(|_| "local_document_unavailable")?
        .join("documents");
    fs::create_dir_all(&directory).map_err(|_| "local_document_unavailable")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .map_err(|_| "local_document_unavailable")?;
    }
    prune_documents(&directory, bytes.len() as u64)?;
    // Random names, create_new and fixed suffixes prevent backend filenames from becoming paths/commands.
    let file = directory.join(format!("{}.{}", Uuid::new_v4(), resource.suffix));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut output = options
        .open(&file)
        .map_err(|_| "local_document_unavailable")?;
    output
        .write_all(&bytes)
        .map_err(|_| "local_document_unavailable")?;
    drop(output);
    // Do not expose an unrestricted OS opener/plugin to the renderer.
    open::that_detached(&file).map_err(|_| "local_document_open_failed")?;
    Ok(())
}

fn prune_documents(directory: &std::path::Path, incoming: u64) -> Result<(), String> {
    let mut files = Vec::new();
    let mut total = 0_u64;
    for entry in fs::read_dir(directory).map_err(|_| "local_document_unavailable")? {
        let entry = entry.map_err(|_| "local_document_unavailable")?;
        let path = entry.path();
        let Some(stem) = path.file_stem().and_then(|v| v.to_str()) else {
            continue;
        };
        if Uuid::parse_str(stem).is_err()
            || !matches!(
                path.extension().and_then(|v| v.to_str()),
                Some("pdf" | "md" | "json")
            )
        {
            continue;
        }
        let metadata = entry.metadata().map_err(|_| "local_document_unavailable")?;
        if !metadata.is_file() {
            continue;
        }
        total = total.saturating_add(metadata.len());
        files.push((
            metadata
                .modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH),
            path,
            metadata.len(),
        ));
    }
    files.sort_by_key(|(modified, _, _)| *modified);
    for (_, path, size) in files {
        if total.saturating_add(incoming) <= 256 * 1024 * 1024 {
            break;
        }
        fs::remove_file(path).map_err(|_| "local_document_cache_full")?;
        total = total.saturating_sub(size);
    }
    if total.saturating_add(incoming) > 256 * 1024 * 1024 {
        return Err("local_document_cache_full".into());
    }
    Ok(())
}

fn main() {
    let (client, credentials) = auth::initialize().expect("local bridge initialization failed");
    if std::env::args().any(|arg| arg == "--check-backend") {
        let result = tauri::async_runtime::block_on(async {
            for path in ["/api/health", "/api/ready"] {
                let response = bridge::request(
                    &client,
                    &ApiRequest {
                        id: Uuid::new_v4().to_string(),
                        path: path.into(),
                        method: "GET".into(),
                        content_type: None,
                        body: None,
                    },
                )
                .await?;
                if !(200..300).contains(&response.status) {
                    return Err::<(), String>("local_backend_not_ready".into());
                }
                let bytes = STANDARD
                    .decode(response.body)
                    .map_err(|_| "invalid_local_response")?;
                let value: serde_json::Value =
                    serde_json::from_slice(&bytes).map_err(|_| "invalid_local_response")?;
                let expected = if path.ends_with("ready") {
                    "ready"
                } else {
                    "ok"
                };
                if value["status"] != expected {
                    return Err("invalid_local_response".into());
                }
            }
            Ok(())
        });
        match result {
            Ok(()) => {
                println!("local_backend_ready");
                return;
            }
            Err(code) => {
                eprintln!("{code}");
                std::process::exit(1);
            }
        }
    }
    let smoke = std::env::args().any(|arg| arg == "--smoke-test");
    tauri::Builder::default().manage(Backend{client,credentials,pending:Arc::new(Pending::default()),document_lock:tokio::sync::Mutex::new(())})
        .invoke_handler(tauri::generate_handler![api_request,cancel_request,run_events,open_resource,local_auth_status,desktop_build_info])
        .setup(move|app|{
            let window=WebviewWindowBuilder::new(app,"main",WebviewUrl::App("index.html".into()))
                .title("Scientific RAGAgent").inner_size(1320.,900.).min_inner_size(800.,560.)
                .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny)
                .on_navigation(|url| {
                    // Webview navigation cannot leave its own bundled/dev origin.
                    matches!(url.scheme(),"tauri")&&url.host_str()==Some("localhost")
                        || matches!(url.scheme(),"http"|"https")&&url.host_str()==Some("tauri.localhost")
                        || cfg!(debug_assertions)&&url.scheme()=="http"&&url.host_str()==Some("127.0.0.1")&&url.port()==Some(1420)
                })
                .on_page_load(move|window,payload|{
                    if smoke && matches!(payload.event(),tauri::webview::PageLoadEvent::Finished){
                        let app=window.app_handle().clone();
                        // Read DOM after rendering; this is only a smoke assertion, not mocked UI/inference.
                        tauri::async_runtime::spawn(async move {
                            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                            let _=window.eval_with_callback("({loaded:document.body.innerText.includes('Scientific RAGAgent'),root:!!document.querySelector('#root')?.firstElementChild,desktop:document.body.innerText.includes('· Desktop'),backend:document.querySelector('.backend-status')?.className})",move|value|{
                                println!("desktop_frontend_loaded:{value}");
                                let parsed:serde_json::Value=serde_json::from_str(&value).unwrap_or_default();
                                app.exit(if parsed["loaded"]==true&&parsed["root"]==true&&parsed["desktop"]==true{0}else{1});
                            });
                        });
                    }
                }).build()?;
            let pending=app.state::<Backend>().pending.clone();
            window.on_window_event(move|event|if matches!(event,tauri::WindowEvent::Destroyed){pending.cancel_all();});
            Ok(())
        }).run(tauri::generate_context!()).expect("desktop runtime failed");
}
