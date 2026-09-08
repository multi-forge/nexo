#!/usr/bin/env python3
"""
AGY Pure Agent Runner (agy_agent_bridge.py)
===========================================
O cérebro 100% autônomo baseado no Antigravity CLI oficial:
1. Recebe a instrução do desenvolvedor
2. Dispara o AGY com permissões completas e formato stream-json
3. Acompanha em tempo real cada ferramenta executada
4. Retorna a resposta final consolidada
"""

import json
import os
import subprocess
import sys
import time

def run_agy_task(prompt: str):
    print("════════════════════════════════════════════════════════════════════")
    print("   DISPARANDO AGENTE AUTÔNOMO ANTIGRAVITY (AGY) NO WINDOWS          ")
    print("════════════════════════════════════════════════════════════════════\n")
    print(f"[Objetivo]: \"{prompt}\"\n")

    cmd = [
        r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe",
        "--print",
        prompt,
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions"
    ]

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    final_response = ""
    tools_called = []

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event_obj = json.loads(line)
            event_type = event_obj.get("event")

            # 1. Atualização de passos / ferramentas
            if event_type == "step_update":
                step = event_obj.get("step_update", {})
                stype = step.get("step_type")
                state = step.get("state")
                tool_name = step.get("tool_name")

                if stype == "tool" and state == "ACTIVE":
                    tname = step.get("tool_info", {}).get("name", tool_name)
                    tools_called.append(tname)
                    print(f"[AGY Tool Call]: Executando '{tname}'...")

            # 2. Resposta final do AGY
            elif event_type == "result":
                res = event_obj.get("result", {})
                status = res.get("status")
                final_response = res.get("response", "")
                dur = res.get("duration_seconds", 0)
                print(f"\n[AGY Concluído]: Status: {status} (Duração: {dur:.2f}s)")

        except json.JSONDecodeError:
            pass

    proc.wait()
    total_elapsed = time.perf_counter() - t0

    print("\n════════════════════════════════════════════════════════════════════")
    print("RESPOSTA FINAL DO AGENTE AGY:")
    print("════════════════════════════════════════════════════════════════════")
    print(final_response)
    print("════════════════════════════════════════════════════════════════════")
    print(f"Tempo Total: {total_elapsed:.2f}s | Ferramentas Acionadas: {len(tools_called)} ({', '.join(set(tools_called))})")
    print("════════════════════════════════════════════════════════════════════\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
    else:
        user_prompt = "Faça uma varredura completa do sistema Windows: verifique espaço livre nos discos, uso de memória RAM, versão do Python e Node.js instalados, e grave um resumo executivo na Área de Trabalho em C:\\Users\\Aluno\\Desktop\\SISTEMA_STATUS.txt."
    
    run_agy_task(user_prompt)
