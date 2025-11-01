#!/bin/bash
# ----------------------------------------------------
# 脚本目标：精确测量 CPU 0 和 2 上的异核对照总指标
# ----------------------------------------------------

# --- 1. 定义变量 ---
CORES="0,2" # 核心列表：绑定到不同的物理核心
PORT_A=5003
PORT_B=5004
# 修正后的事件列表：使用 cycle_activity.stalls_total 和 idq_uops_not_delivered.core
EVENT_LIST="cycles,instructions,cache-misses,cycle_activity.stalls_total,idq_uops_not_delivered.core,cpu-clock,mem_load_retired.l3_hit,mem_load_retired.l3_miss,cycle_activity.stalls_l3_miss,memory_activity.stalls_l2_miss,mem_load_retired.l1_miss,mem_load_retired.l2_miss,mem_inst_retired.stlb_miss_loads,mem_load_l3_miss_retired.local_dram,mem_load_l3_hit_retired.xsnp_fwd"
MATRIX_SIZE=20000

# --- 2. 启动 perf 监控 ---
echo "Starting background perf stat on cores $CORES..."
# -C 0,2: 监控两个独立物理核心的总事件。
sudo nohup perf stat -C $CORES -e $EVENT_LIST -- sleep 1000 > perf_output_iso.txt 2>&1 &
PERF_PID=$!

sleep 1 
echo "Perf PID: $PERF_PID. Starting competing tasks..."

# --- 3. 启动竞争任务并等待其完成 ---
(
    CURL_A_START=$(date +%s.%N)
    curl -X POST http://localhost:$PORT_A/run -d '{"param": '$MATRIX_SIZE'}' 
    CURL_A_END=$(date +%s.%N)
    echo "Task A Latency: $(echo "$CURL_A_END - $CURL_A_START" | bc) seconds"
) &
CURL_JOB_A=$!

(
    CURL_B_START=$(date +%s.%N)
    curl -X POST http://localhost:$PORT_B/run -d '{"param": '$MATRIX_SIZE'}'
    CURL_B_END=$(date +%s.%N)
    echo "Task B Latency: $(echo "$CURL_B_END - $CURL_B_START" | bc) seconds"
) &
CURL_JOB_B=$!

wait $CURL_JOB_A $CURL_JOB_B
echo "All matmul tasks finished."

# --- 4. 任务结束后，立即终止 perf 进程 ---
# 确保在两个任务结束时立即停止 perf，以获得精确数据。
echo "Stopping perf process $PERF_PID to finalize data collection..."
sudo kill -SIGINT $PERF_PID

wait $PERF_PID 2>/dev/null
echo "Perf collection finished. Results are in perf_output_iso.txt"
