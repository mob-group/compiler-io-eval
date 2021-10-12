#!/usr/bin/env bash

python3.9 -m venv venv
source venv/bin/activate

bash get-synthesis-eval-data.sh
