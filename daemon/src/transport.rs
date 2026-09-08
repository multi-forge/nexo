use crate::host_runtime::HostRuntime;
use crate::policy::PolicyEngine;
use crate::supervisor::AgentSupervisor;
use serde_json::json;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

pub struct TransportReceiver {
    policy: Arc<PolicyEngine>,
    supervisor: Arc<AgentSupervisor>,
}

impl TransportReceiver {
    pub fn new(policy: Arc<PolicyEngine>, supervisor: Arc<AgentSupervisor>) -> Self {
        Self { policy, supervisor }
    }

    pub async fn run_server(&self) -> anyhow::Result<()> {
        let addr = self.policy.get_listen_addr();
        let listener = TcpListener::bind(addr).await?;
        println!("[nexo-daemon] Servidor escutando em http://{}", addr);

        loop {
            let (stream, _) = listener.accept().await?;
            let policy = Arc::clone(&self.policy);
            let supervisor = Arc::clone(&self.supervisor);

            tokio::spawn(async move {
                if let Err(e) = handle_connection(stream, policy, supervisor).await {
                    eprintln!("[nexo-daemon] Erro na conexao: {}", e);
                }
            });
        }
    }
}

async fn handle_connection(
    mut stream: TcpStream,
    policy: Arc<PolicyEngine>,
    supervisor: Arc<AgentSupervisor>,
) -> anyhow::Result<()> {
    let mut buffer = [0u8; 4096];
    let n = stream.read(&mut buffer).await?;
    if n == 0 {
        return Ok(());
    }

    let req_str = String::from_utf8_lossy(&buffer[..n]);
    let mut lines = req_str.lines();
    let request_line = lines.next().unwrap_or("");
    let parts: Vec<&str> = request_line.split_whitespace().collect();

    if parts.len() < 2 {
        return Ok(());
    }

    let method = parts[0];
    let path = parts[1];

    let (status_code, body) = match (method, path) {
        ("GET", "/health") => (
            "200 OK",
            json!({ "status": "healthy", "service": "nexo-daemon", "version": "0.1.0" }),
        ),
        ("GET", "/api/v1/hardware") => match HostRuntime::get_hardware_inventory() {
            Ok(inv) => ("200 OK", serde_json::to_value(inv)?),
            Err(e) => (
                "500 Internal Server Error",
                json!({ "error": e.to_string() }),
            ),
        },
        ("POST", "/api/v1/tasks") => {
            // Find body in HTTP payload
            let body_part = req_str.split("\r\n\r\n").nth(1).unwrap_or("{}");
            let req_json: serde_json::Value = serde_json::from_str(body_part).unwrap_or(json!({}));
            let prompt = req_json["prompt"].as_str().unwrap_or("");
            let workspace = req_json["workspace"].as_str().unwrap_or("C:/Users/Aluno");
            let model = req_json["model"].as_str().unwrap_or("flash_lite");

            match policy.check_workspace(workspace) {
                Ok(_) => match supervisor.new_conversation(prompt, model) {
                    Ok(conv_id) => (
                        "200 OK",
                        json!({ "status": "dispatched", "conversation_id": conv_id, "workspace": workspace }),
                    ),
                    Err(e) => (
                        "500 Internal Server Error",
                        json!({ "error": e.to_string() }),
                    ),
                },
                Err(err) => (
                    "403 Forbidden",
                    json!({ "error": "Violacao de politica", "details": err }),
                ),
            }
        }
        _ => (
            "404 Not Found",
            json!({ "error": "Endpoint desconhecido", "path": path }),
        ),
    };

    let body_str = body.to_string();
    let response = format!(
        "HTTP/1.1 {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        status_code,
        body_str.len(),
        body_str
    );

    stream.write_all(response.as_bytes()).await?;
    stream.flush().await?;
    Ok(())
}
