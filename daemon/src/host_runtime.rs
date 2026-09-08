#![allow(dead_code)]
use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Debug, Serialize, Deserialize)]
pub struct HardwareInventory {
    pub hostname: String,
    pub os: String,
    pub cpu: String,
    pub cpu_cores: u32,
    pub cpu_threads: u32,
    pub ram_total_gb: f64,
    pub gpus: Vec<String>,
    pub primary_disk_free_gb: f64,
    pub primary_disk_total_gb: f64,
}

pub struct HostRuntime;

impl HostRuntime {
    pub fn get_hardware_inventory() -> anyhow::Result<HardwareInventory> {
        let ps_script = r#"
            $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, NumberOfCores, NumberOfLogicalProcessors
            $mem = (Get-CimInstance Win32_PhysicalMemory | Measure-Object Capacity -Sum).Sum / 1GB
            $os = (Get-CimInstance Win32_OperatingSystem).Caption
            $gpus = @(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name)
            $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
            $freeGb = $disk.FreeSpace / 1GB
            $totalGb = $disk.Size / 1GB

            [PSCustomObject]@{
                Hostname = $env:COMPUTERNAME
                OS = $os.Trim()
                Cpu = $cpu.Name.Trim()
                CpuCores = [int]$cpu.NumberOfCores
                CpuThreads = [int]$cpu.NumberOfLogicalProcessors
                RamGb = [math]::Round($mem, 2)
                Gpus = $gpus
                DiskFreeGb = [math]::Round($freeGb, 2)
                DiskTotalGb = [math]::Round($totalGb, 2)
            } | ConvertTo-Json -Compress
        "#;

        let output = Command::new("powershell")
            .args(["-NoProfile", "-Command", ps_script])
            .output()?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("Falha ao inspecionar hardware via powershell: {}", err);
        }

        let json_str = String::from_utf8_lossy(&output.stdout);
        let v: serde_json::Value = serde_json::from_str(json_str.trim())?;

        let gpus = v["Gpus"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|g| g.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_else(|| {
                if let Some(single) = v["Gpus"].as_str() {
                    vec![single.to_string()]
                } else {
                    vec![]
                }
            });

        Ok(HardwareInventory {
            hostname: v["Hostname"].as_str().unwrap_or("UNKNOWN").to_string(),
            os: v["OS"].as_str().unwrap_or("Windows").to_string(),
            cpu: v["Cpu"].as_str().unwrap_or("Generic CPU").to_string(),
            cpu_cores: v["CpuCores"].as_u64().unwrap_or(0) as u32,
            cpu_threads: v["CpuThreads"].as_u64().unwrap_or(0) as u32,
            ram_total_gb: v["RamGb"].as_f64().unwrap_or(0.0),
            gpus,
            primary_disk_free_gb: v["DiskFreeGb"].as_f64().unwrap_or(0.0),
            primary_disk_total_gb: v["DiskTotalGb"].as_f64().unwrap_or(0.0),
        })
    }

    pub fn set_volume(step: i32) -> anyhow::Result<String> {
        let script = format!(
            "$wsh = New-Object -ComObject Wscript.Shell; 1..{} | ForEach-Object {{ $wsh.SendKeys([char]174) }}",
            step.abs()
        );
        Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .output()?;
        Ok(format!("Volume ajustado (passos: {})", step))
    }
}

