#!/bin/bash
# Exibe uma mensagem no terminal
echo "Sincronizando o git..."
git pull origin main
echo "reiniciando o  serviço da aplicação..."
sudo systemctl restart supervisor &&  sudo systemctl restart nginx