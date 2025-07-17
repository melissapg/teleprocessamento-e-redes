from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI
from mininet.node import OVSController

from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import os
import math
import numpy as np
import matplotlib.pyplot as plt

parser = ArgumentParser(description="TCP Competition tests")

parser.add_argument('--bw-host', '-B',
                    type=float,
                    help="Bandwidth of host links (Mb/s)",
                    default=1000)

parser.add_argument('--bw-net', '-b',
                    type=float,
                    help="Bandwidth of bottleneck (network) link (Mb/s)",
                    required=True)

parser.add_argument('--delay',
                    type=float,
                    help="Link propagation delay (ms)",
                    required=True)

parser.add_argument('--dir', '-d',
                    help="Directory to store outputs",
                    required=True)

parser.add_argument('--time', '-t',
                    help="Duration (sec) to run the experiment",
                    type=int,
                    default=60)

parser.add_argument('--maxq',
                    type=int,
                    help="Max buffer size of network interface in packets",
                    default=100)

# Argumentos para controlar os fluxos
parser.add_argument('--num-flows-reno',
                    type=int,
                    help="Número de fluxos TCP Reno",
                    default=0)

parser.add_argument('--num-flows-bbr',
                    type=int,
                    help="Número de fluxos TCP BBR",
                    default=0)

# Expt parameters
args = parser.parse_args()

class CompetitionTopo(Topo):
    "Topologia para experimento de competição entre TCP Reno e TCP BBR."

    def build(self):
        # Criar dois hosts
        h1 = self.addHost("h1")
        h2 = self.addHost("h2")

        # Criar switch
        switch = self.addSwitch('s0')

        # Adicionar links com características apropriadas
        self.addLink(h1, switch, bw=args.bw_host, delay=args.delay)
        self.addLink(switch, h2, bw=args.bw_net, delay=args.delay, max_queue_size=args.maxq)

def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

def start_ping(net):
    h1 = net.get("h1")
    h2 = net.get("h2")

    print("Iniciando ping...")
    h1.popen(f"ping {h2.IP()} -i 0.1 -c {int(args.time * 10)} > {args.dir}/ping.txt", shell=True)

def start_multiple_flows(net):
    h1 = net.get("h1")
    h2 = net.get("h2")
    
    print("Iniciando servidor iperf...")
    server_procs = []
    
    # Inicia múltiplos servidores para cada fluxo
    for i in range(args.num_flows_reno + args.num_flows_bbr):
        port = 5001 + i
        server_proc = h2.popen(f"iperf -s -p {port} -w 16m")
        server_procs.append(server_proc)
    
    # Atraso para garantir que os servidores estejam prontos
    sleep(1)
    
    client_procs = []
    
    # Inicia fluxos TCP Reno
    for i in range(args.num_flows_reno):
        port = 5001 + i
        print(f"Iniciando fluxo TCP Reno {i+1}...")
        
        cmd = f"iperf -c {h2.IP()} -p {port} -t {args.time} -i 1 -Z reno > {args.dir}/reno_flow_{i+1}.txt"
        client_proc = h1.popen(cmd, shell=True)
        client_procs.append(client_proc)
    
    # Inicia fluxos TCP BBR
    for i in range(args.num_flows_bbr):
        port = 5001 + args.num_flows_reno + i
        print(f"Iniciando fluxo TCP BBR {i+1}...")
        
        # Verificar se BBR está disponível
        h1.cmd("modprobe tcp_bbr")
        
        cmd = f"iperf -c {h2.IP()} -p {port} -t {args.time} -i 1 -Z bbr > {args.dir}/bbr_flow_{i+1}.txt"
        client_proc = h1.popen(cmd, shell=True)
        client_procs.append(client_proc)
    
    return server_procs + client_procs

def parse_iperf_output(filename):
    """Analisa a saída do iperf para extrair vazão ao longo do tempo."""
    throughputs = []
    times = []
    
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
            
        for line in lines:
            if 'sec' in line and 'Bytes' in line:
                parts = line.split()
                # Formato típico: [ID] intervalo transfer bandwidth
                try:
                    time_interval = parts[2].split('-')
                    time = float(time_interval[1])
                    # Busca o valor da vazão (geralmente na posição 6 ou 8)
                    for i, part in enumerate(parts):
                        if 'Mbits/sec' in part or 'Mbits/sec' in (parts[i+1] if i+1 < len(parts) else ''):
                            throughput = float(parts[i])
                            throughputs.append(throughput)
                            times.append(time)
                            break
                except (ValueError, IndexError):
                    continue
    except FileNotFoundError:
        print(f"Arquivo {filename} não encontrado")
    
    return times, throughputs

def analyze_results():
    """Analisa os resultados da competição e gera gráficos."""
    # Análise de vazão de fluxos Reno
    reno_throughputs = []
    for i in range(args.num_flows_reno):
        filename = f"{args.dir}/reno_flow_{i+1}.txt"
        times, throughputs = parse_iperf_output(filename)
        if throughputs:
            reno_throughputs.append(throughputs)
    
    # Análise de vazão de fluxos BBR
    bbr_throughputs = []
    for i in range(args.num_flows_bbr):
        filename = f"{args.dir}/bbr_flow_{i+1}.txt"
        times, throughputs = parse_iperf_output(filename)
        if throughputs:
            bbr_throughputs.append(throughputs)
    
    # Se não houver dados para analisar, retornar
    if not reno_throughputs and not bbr_throughputs:
        print("Não há dados suficientes para análise")
        return
    
    # Calcular vazão média para cada algoritmo
    avg_reno = np.mean([np.mean(tput) for tput in reno_throughputs]) if reno_throughputs else 0
    avg_bbr = np.mean([np.mean(tput) for tput in bbr_throughputs]) if bbr_throughputs else 0
    
    total_throughput = avg_reno + avg_bbr
    reno_percentage = (avg_reno / total_throughput * 100) if total_throughput > 0 else 0
    bbr_percentage = (avg_bbr / total_throughput * 100) if total_throughput > 0 else 0
    
    print("\n=== RESULTADOS DA COMPETIÇÃO ===")
    print(f"Vazão média TCP Reno: {avg_reno:.2f} Mbps ({reno_percentage:.1f}% do total)")
    print(f"Vazão média TCP BBR: {avg_bbr:.2f} Mbps ({bbr_percentage:.1f}% do total)")
    print(f"Vazão total: {total_throughput:.2f} Mbps")
    
    # Criar gráfico de barras de vazão média
    plt.figure(figsize=(10, 6))
    algoritmos = []
    vazoes = []
    
    # Adicionar barras para cada fluxo Reno
    for i, tput in enumerate(reno_throughputs):
        algoritmos.append(f"Reno {i+1}")
        vazoes.append(np.mean(tput))
    
    # Adicionar barras para cada fluxo BBR
    for i, tput in enumerate(bbr_throughputs):
        algoritmos.append(f"BBR {i+1}")
        vazoes.append(np.mean(tput))
    
    plt.bar(algoritmos, vazoes, color=['blue' if 'Reno' in alg else 'red' for alg in algoritmos])
    plt.title('Vazão média por fluxo')
    plt.ylabel('Vazão (Mbps)')
    plt.xlabel('Algoritmo e número do fluxo')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(f"{args.dir}/throughput_comparison.png")
    
    # Gráfico de vazão agregada por algoritmo
    plt.figure(figsize=(8, 6))
    plt.bar(['TCP Reno', 'TCP BBR'], [avg_reno, avg_bbr], color=['blue', 'red'])
    plt.title('Vazão média agregada por algoritmo')
    plt.ylabel('Vazão (Mbps)')
    plt.xlabel('Algoritmo de congestionamento')
    plt.savefig(f"{args.dir}/algorithm_comparison.png")
    
    # Analisar ping (latência)
    try:
        with open(f"{args.dir}/ping.txt", 'r') as f:
            ping_lines = f.readlines()
        
        rtts = []
        for line in ping_lines:
            if "time=" in line:
                try:
                    rtt = float(line.split("time=")[1].split()[0])
                    rtts.append(rtt)
                except (ValueError, IndexError):
                    continue
        
        if rtts:
            avg_rtt = np.mean(rtts)
            max_rtt = np.max(rtts)
            min_rtt = np.min(rtts)
            
            print(f"\n=== ANÁLISE DE LATÊNCIA ===")
            print(f"RTT médio: {avg_rtt:.2f} ms")
            print(f"RTT mínimo: {min_rtt:.2f} ms")
            print(f"RTT máximo: {max_rtt:.2f} ms")
            
            # Gráfico de RTT ao longo do tempo
            plt.figure(figsize=(12, 6))
            plt.plot(rtts)
            plt.title('RTT ao longo do tempo')
            plt.ylabel('RTT (ms)')
            plt.xlabel('Número da amostra')
            plt.grid(True)
            plt.savefig(f"{args.dir}/rtt_over_time.png")
            
            # Histograma de RTT
            plt.figure(figsize=(10, 6))
            plt.hist(rtts, bins=30)
            plt.title('Distribuição de RTT')
            plt.ylabel('Frequência')
            plt.xlabel('RTT (ms)')
            plt.grid(True)
            plt.savefig(f"{args.dir}/rtt_histogram.png")
    
    except FileNotFoundError:
        print("Arquivo de ping não encontrado")

def competition():
    """Função principal para executar o experimento de competição."""
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)
    
    # Verificar se os argumentos são válidos
    if args.num_flows_reno == 0 and args.num_flows_bbr == 0:
        print("Erro: Especifique pelo menos um fluxo TCP Reno ou BBR")
        return
    
    # Imprimir informações do experimento
    print("\n=== EXPERIMENTO DE COMPETIÇÃO TCP ===")
    print(f"Fluxos TCP Reno: {args.num_flows_reno}")
    print(f"Fluxos TCP BBR: {args.num_flows_bbr}")
    print(f"Duração: {args.time} segundos")
    print(f"Largura de banda do gargalo: {args.bw_net} Mbps")
    print(f"Atraso: {args.delay} ms")
    print(f"Tamanho máximo da fila: {args.maxq} pacotes")
    
    # Criar topologia e iniciar rede
    topo = CompetitionTopo()
    
    # Usando Windows com Vagrant descomentar a linha:
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    
    # Usando WSL descomentar a linha:	
    # net = Mininet(topo=topo, link=TCLink, controller=OVSController)
    
    net.start()
    
    # Mostrar conexões entre nós
    dumpNodeConnections(net.hosts)
    
    # Teste básico de ping
    net.pingAll()
    
    # Iniciar monitoramento da fila
    qmon = start_qmon(iface='s0-eth2', outfile=f"{args.dir}/q.txt")
    
    # Iniciar ping para medir RTT
    start_ping(net)
    
    # Iniciar fluxos TCP Reno e BBR
    procs = start_multiple_flows(net)
    
    # Esperar pela conclusão dos fluxos
    print(f"\nExperimento em andamento. Aguarde {args.time} segundos...")
    start_time = time()
    while True:
        sleep(5)
        now = time()
        delta = now - start_time
        if delta > args.time:
            break
        print(f"{args.time - delta:.1f}s restantes...")
    
    # Encerrar processos e limpar
    print("\nFinalizando experimento...")
    for proc in procs:
        proc.terminate()
    
    qmon.terminate()
    net.stop()
    
    # Encerrar processos do iperf que possam estar em execução
    Popen("pkill -f iperf", shell=True).wait()
    
    # Analisar resultados
    print("\nAnalisando resultados...")
    analyze_results()
    
    print(f"\nExperimento concluído! Os resultados estão no diretório '{args.dir}'")

if __name__ == "__main__":
    competition()