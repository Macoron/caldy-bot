#!/bin/bash
set -e

ln -sf "$(pwd)/caldy-bot.service" /etc/systemd/system/caldy-bot.service
systemctl daemon-reload
systemctl enable caldy-bot
systemctl restart caldy-bot
