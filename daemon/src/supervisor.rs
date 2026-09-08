#![allow(dead_code)]
use serde_json::Value;
use std::process::Command;

pub struct AgentSupervisor {
    agy_path: String,
}

impl AgentSupervisor {
    pub fn new() -> Self {
        Self {
            agy_path: r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe".to_string(),
        }
    }

    pub fn new_conversation(&self, prompt: &str, model: &str) -> anyhow::Result<String> {
        let model_flag = format!("--model={}", model);
        let output = Command::new(&self.agy_path)
            .args(["agentapi", "new-conversation", &model_flag, prompt])
            .output()?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("agentapi new-conversation falhou: {}", err);
        }

        let out_str = String::from_utf8_lossy(&output.stdout);
        let val: Value = serde_json::from_str(&out_str)?;
        let conv_id = val["response"]["newConversation"]["conversationId"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("Formato inesperado de conversationId: {}", out_str))?;

        Ok(conv_id.to_string())
    }

    pub fn send_message(&self, conv_id: &str, message: &str) -> anyhow::Result<()> {
        let output = Command::new(&self.agy_path)
            .args(["agentapi", "send-message", conv_id, message])
            .output()?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("agentapi send-message falhou: {}", err);
        }

        Ok(())
    }
}

