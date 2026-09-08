mod host_runtime;
mod policy;
mod supervisor;
mod transport;

use host_runtime::HostRuntime;
use policy::PolicyEngine;
use supervisor::AgentSupervisor;
use transport::TransportReceiver;

use std::env;
use std::process::Command;
use std::sync::Arc;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = env::args().collect();
    let command = args.get(1).map(|s| s.as_str()).unwrap_or("run");

    let policy_path = "policy.toml";
    let policy = Arc::new(PolicyEngine::load_from_file(policy_path)?);
    let supervisor = Arc::new(AgentSupervisor::new());

    match command {
        "run" => {
            println!("============================================================");
            println!("           NEXO DAEMON - Local Execution Agent              ");
            println!("============================================================");
            println!("Politica carregada de: {}", policy_path);
            let receiver = TransportReceiver::new(policy, supervisor);
            receiver.run_server().await?;
        }
        "hardware" => {
            let inv = HostRuntime::get_hardware_inventory()?;
            println!("{}", serde_json::to_string_pretty(&inv)?);
        }
        "check-policy" => {
            let target = args.get(2).map(|s| s.as_str()).unwrap_or("C:/Users/Aluno");
            match policy.check_workspace(target) {
                Ok(_) => println!("[OK] Caminho '{}' autorizado pela politica.", target),
                Err(err) => println!("[REJEITADO] {}", err),
            }
        }
        "process-audio" => {
            let audio_file = args
                .get(2)
                .map(|s| s.as_str())
                .unwrap_or(r"C:\Users\Aluno\Downloads\Generated Audio September 07, 2026 - 9_36PM.wav");
            
            println!("============================================================");
            println!("     NEXO DAEMON - Pipeline de Audio & Orquestracao         ");
            println!("============================================================");
            println!("Arquivo de entrada: {}", audio_file);

            // Execute the Python-based Chirp/Gemini/Edge-TTS voice bridge
            let status = Command::new("python")
                .args(["nexo_orchestrator.py", audio_file])
                .status()?;

            if !status.success() {
                anyhow::bail!("Falha na execucao do pipeline de audio");
            }
        }
        _ => {
            println!("Uso: nexo-daemon [run | hardware | check-policy <path> | process-audio <wav_file>]");
        }
    }

    Ok(())
}
