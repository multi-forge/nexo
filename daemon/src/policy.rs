#![allow(dead_code)]
use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Deserialize, Clone)]
pub struct ServerConfig {
    pub listen_addr: String,
}

#[derive(Debug, Deserialize, Clone)]
pub struct WorkspaceConfig {
    pub allowed: Vec<String>,
    pub denied: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct HostActionsAllowlist {
    pub binaries: Vec<String>,
    #[serde(default = "default_volume")]
    pub max_volume_step: u32,
}

fn default_volume() -> u32 {
    15
}

#[derive(Debug, Deserialize, Clone)]
pub struct HostActionsConfig {
    pub allowlist: HostActionsAllowlist,
}

#[derive(Debug, Deserialize, Clone)]
pub struct SecurityConfig {
    pub unattended_risk_ceiling: String,
    pub require_explicit_confirmation_for: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct PolicyConfig {
    pub server: ServerConfig,
    pub workspaces: WorkspaceConfig,
    pub host_actions: HostActionsConfig,
    pub security: SecurityConfig,
}

#[derive(Clone)]
pub struct PolicyEngine {
    config: PolicyConfig,
}

impl PolicyEngine {
    pub fn load_from_file(path: &str) -> anyhow::Result<Self> {
        let content = fs::read_to_string(path)?;
        let config: PolicyConfig = toml::from_str(&content)?;
        Ok(Self { config })
    }

    pub fn check_workspace(&self, target: &str) -> Result<(), String> {
        let normalized = target.replace('\\', "/").to_lowercase();
        
        // 1. Check explicit denies
        for denied in &self.config.workspaces.denied {
            let d = denied.replace('\\', "/").to_lowercase();
            if normalized.starts_with(&d) {
                return Err(format!("Acesso negado pela politica: '{}' esta na lista de caminhos proibidos", target));
            }
        }

        // 2. Check allowed list
        for allowed in &self.config.workspaces.allowed {
            let a = allowed.replace('\\', "/").to_lowercase();
            if normalized.starts_with(&a) {
                return Ok(());
            }
        }

        Err(format!("Acesso negado: '{}' nao pertence a nenhum workspace homologado", target))
    }

    pub fn check_binary(&self, binary: &str) -> Result<(), String> {
        let bin_name = Path::new(binary)
            .file_name()
            .and_then(|f| f.to_str())
            .unwrap_or(binary)
            .trim_end_matches(".exe")
            .to_lowercase();

        for allowed in &self.config.host_actions.allowlist.binaries {
            if allowed.to_lowercase() == bin_name {
                return Ok(());
            }
        }

        Err(format!("Binario '{}' rejeitado pelo policy engine: fora da allowlist", binary))
    }

    pub fn get_listen_addr(&self) -> &str {
        &self.config.server.listen_addr
    }
}
