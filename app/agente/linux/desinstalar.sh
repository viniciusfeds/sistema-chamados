#!/bin/sh
# Remove o Nexus Agente. Rode como root: sudo sh desinstalar.sh
systemctl disable --now nexus-agente 2>/dev/null || true
rm -f /etc/systemd/system/nexus-agente.service
systemctl daemon-reload 2>/dev/null || true
rm -rf /opt/nexus-agente /etc/nexus-agente
echo "Nexus Agente removido."
