#!/bin/bash

# Este script executa o experimento de competição entre TCP Reno e TCP BBR
# Execute com sudo: sudo bash run_competition.sh

time=90
bwnet=1.5
bwhost=1000
delay=5
maxq=100

# Cenário 1: 1 fluxo Reno vs 1 fluxo BBR
dir=comp-reno1-bbr1
python3 competition.py -B $bwhost -b $bwnet --delay $delay -d $dir --time $time --maxq $maxq --num-flows-reno 1 --num-flows-bbr 1

# Cenário 2: 2 fluxos Reno vs 2 fluxos BBR
dir=comp-reno2-bbr2
python3 competition.py -B $bwhost -b $bwnet --delay $delay -d $dir --time $time --maxq $maxq --num-flows-reno 2 --num-flows-bbr 2

# Cenário 3: 2 fluxos Reno vs 1 fluxo BBR
dir=comp-reno2-bbr1
python3 competition.py -B $bwhost -b $bwnet --delay $delay -d $dir --time $time --maxq $maxq --num-flows-reno 2 --num-flows-bbr 1