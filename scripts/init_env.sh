#!/usr/bin/env bash
set -euo pipefail
cp .env.example .env
SECRET=$(openssl rand -hex 64)
sed -i.bak "s/CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_64/${SECRET}/" .env
rm -f .env.bak
printf '.env created\n'
