#!/bin/bash
# ----------------------------------------------------
# 脚本目标：精确测量 CPU 0 和 64 上的超线程竞争总指标
# ----------------------------------------------------

# --- 1. 定义变量 ---
CORES="0,64"
PORT_A=5001
PORT_B=5002
EVENT_LIST="cycles,instructions,cache-misses,cycle_activity.stalls_total,idq_uops_not_delivered.core,cpu-clock,mem_load_retired.l3_hit,mem_load_retired.l3_miss,cycle_activity.stalls_l3_miss,memory_activity.stalls_l2_miss,mem_load_retired.l1_miss,mem_load_retired.l2_miss,mem_inst_retired.stlb_miss_loads,mem_load_l3_miss_retired.local_dram,mem_load_l3_hit_retired.xsnp_fwd"
MATRIX_SIZE=20000

# --- 2. 启动 perf 监控 ---
echo "Starting background perf stat on cores $CORES..."
# nohup: 确保 perf 进程不会因终端关闭而停止。
# > perf_output_ht.txt 2>&1: 将 perf 结果输出到文件。
# -- sleep 180: 让 perf 持续监控 3000 秒（一个远超任务时间的保守值）。
# &: 将 perf 进程放入后台运行。
sudo nohup perf stat -C $CORES -e $EVENT_LIST -- sleep 3000 > perf_output_ht.txt 2>&1 &
PERF_PID=$! # 记录 perf 进程的 PID。

sleep 1 
echo "Perf PID: $PERF_PID. Starting competing tasks..."

# --- 3. 启动竞争任务并等待其完成 ---
# ( command ) &: 将命令组放入后台，并允许我们使用 wait $! 等待它们。

(
    # 任务 A (CPU 0)
    CURL_A_START=$(date +%s.%N) # 记录任务 A 开始的精确时间。
    curl -X POST http://localhost:$PORT_A/run -d '{"param": '$MATRIX_SIZE'}' 
    CURL_A_END=$(date +%s.%N) # 记录任务 A 结束的精确时间。
    # bc 用于浮点数计算任务 A 的延迟。
    echo "Task A Latency: $(echo "$CURL_A_END - $CURL_A_START" | bc) seconds"
) &
CURL_JOB_A=$! # 记录任务 A 的 Shell Job ID。

(
    # 任务 B (CPU 64)
    CURL_B_START=$(date +%s.%N)
    curl -X POST http://localhost:$PORT_B/run -d '{"param": '$MATRIX_SIZE'}'
    CURL_B_END=$(date +%s.%N)
    echo "Task B Latency: $(echo "$CURL_B_END - $CURL_B_START" | bc) seconds"
) &
CURL_JOB_B=$!

# 核心操作：等待所有 matmul 任务的 HTTP 请求全部返回（即计算完成）。
wait $CURL_JOB_A $CURL_JOB_B
echo "All matmul tasks finished."

# --- 4. 任务结束后，立即终止 perf 进程 ---
# kill -SIGINT $PERF_PID: 发送 SIGINT 信号（模拟 Ctrl+C），安全地终止 perf 进程，让它打印出收集到的有效数据。
echo "Stopping perf process $PERF_PID to finalize data collection..."
sudo kill -SIGINT $PERF_PID

wait $PERF_PID 2>/dev/null # 等待 perf 进程完全退出。
echo "Perf collection finished. Results are in perf_output_ht.txt"