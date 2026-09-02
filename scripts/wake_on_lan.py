#!/usr/bin/env python3
"""
Script de Wake-on-LAN e Intel vPro para inicialização remota do PC via Orange Pi
"""

import socket
import sys

def send_wol(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9):
    mac_clean = mac_address.replace(":", "").replace("-", "").replace(".", "")
    if len(mac_clean) != 12:
        raise ValueError("Formato MAC inválido")
    
    # Pacote mágico WoL: 6x 0xFF seguido de 16 repetições do MAC
    data = bytes.fromhex("FF" * 6 + mac_clean * 16)
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(data, (broadcast_ip, port))
    
    print(f"⚡ Magic Packet WoL enviado para {mac_address} via {broadcast_ip}:{port}")

if __name__ == "__main__":
    target_mac = sys.argv[1] if len(sys.argv) > 1 else "AA:BB:CC:DD:EE:FF"
    send_wol(target_mac)
