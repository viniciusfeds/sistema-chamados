#!/bin/sh
# Nexus Agente __NEXUS_VERSAO__ — instalador para Linux (systemd). Rode como root: sudo sh instalar.sh
set -e
DESTINO=/opt/nexus-agente
if [ "$(id -u)" != "0" ]; then echo "Rode como root: sudo sh instalar.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "Instale o python3 (ex.: apt install python3)"; exit 1; }
command -v ping >/dev/null || echo "Aviso: comando ping não encontrado (apt install iputils-ping). Equipamentos sem porta não serão testados."
mkdir -p "$DESTINO"
cp "$(dirname "$0")/nexus_agente.py" "$DESTINO/nexus_agente.py"
chmod 755 "$DESTINO/nexus_agente.py"
NEXUS_DADOS=/etc/nexus-agente python3 "$DESTINO/nexus_agente.py" ativar "$1" "$2" </dev/tty
cat > /etc/systemd/system/nexus-agente.service <<UNIT
[Unit]
Description=Nexus Agente - monitoramento local (TecnosIT)
After=network-online.target
Wants=network-online.target

[Service]
Environment=NEXUS_DADOS=/etc/nexus-agente
ExecStart=/usr/bin/python3 $DESTINO/nexus_agente.py rodar
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now nexus-agente
echo
echo "  Pronto! Serviço nexus-agente instalado e rodando."
echo "  Status: systemctl status nexus-agente   ·   Log: /etc/nexus-agente/agente.log"
