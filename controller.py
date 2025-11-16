# controller.py
from flask import Flask, json, request, jsonify
import threading
from function_manager import FunctionManager #
import atexit
import time
import requests  # <-- 需要导入 requests
from concurrent.futures import ThreadPoolExecutor # <-- 新增导入
import subprocess
import os
import signal

app = Flask(__name__) #

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERF_LOG_DIR = os.path.join(BASE_DIR, "storage/perf_logs")

function_managers = {} #
manager_lock = threading.Lock() #

# --- create_manager 接口 (保持不变) ---
@app.route('/create_manager', methods=['POST']) #
def create_manager():
    # ... 您的 create_manager 函数代码保持不变 ...
    # (确保它能接收 host_storage_path)
    body = request.get_json(silent=True) or {} #
    function_name = body.get("function_name") #
    if not function_name:
        return jsonify({"error": "function_name required"}), 400

    with manager_lock:
        if function_name in function_managers:
            return jsonify({"status": "exists", "message": f"Manager {function_name} already exists."}), 200

        image_name = body.get("image_name", "myimage:latest") #
        container_port = int(body.get("container_port", 5000)) #
        
        # --- 确保这部分代码存在 (来自我们上次的修改) ---
        host_storage_path = body.get("host_storage_path", None)
        # --- 结束 ---
        
        host_port_start = int(body.get("host_port_start", 8000)) #
        idle_timeout = int(body.get("idle_timeout", 300)) #
        min_idle = int(body.get("min_idle_containers", 0)) #
        max_containers = body.get("max_containers", None) #
        if max_containers is not None:
            max_containers = int(max_containers)

        manager = FunctionManager( #
            function_name=function_name,
            image_name=image_name,
            container_port=container_port,
            host_storage_path=host_storage_path, # <-- 确保传入
            host_port_start=host_port_start,
            idle_timeout=idle_timeout,
            min_idle_containers=min_idle
        )
        function_managers[function_name] = manager #
        return jsonify({"status": "created", "function": function_name}), 201 #

# --- 替换旧的 _dispatch_request 函数 ---
def _dispatch_request(function_name, payload, run_perf=True):
    """
    内部共享逻辑：为函数获取、初始化、运行(带perf)并释放一个容器。
    返回: (result_payload, container_id)
    会抛出异常如果失败。
    """
    print(f"[_dispatch_request] 正在为 '{function_name}' 寻找 manager...")
    with manager_lock:
        if function_name not in function_managers:
            print(f"[_dispatch_request] 错误: 未知的函数 {function_name}")
            raise Exception(f"未知的函数: {function_name}")
        manager = function_managers[function_name]

    print(f"[_dispatch_request] 正在为 '{function_name}' 获取容器...")
    host_port, container_id = manager.get_container_for_request()
    if not host_port:
        print(f"[_dispatch_request] 错误: 无法获取容器 {function_name}")
        raise Exception(f"无法获取容器 {function_name}")

    perf_process = None
    output_file = ""
    pid = None

    try:
        # --- 1. 运行 INIT (现在是第一步，没有 perf) ---
        try:
            init_data = {"action": function_name}
            manager_url = f"http://127.0.0.1:{host_port}"
            print(f"[_dispatch_request] 正在为 {container_id[:12]} 调用 {manager_url}/init")
            requests.post(f"{manager_url}/init", json=init_data, timeout=10)
        except Exception as e:
            # init 失败仍然是非致命的
            print(f"[_dispatch_request] init 错误 (非致命): {e}")


        # --- 2. 启动 PERF (新位置：在 init 之后, run 之前) ---
        if run_perf:
            try:
                # 2a. 获取 PID
                with manager.lock:
                    container_obj = manager.containers[container_id]["container_obj"]
                    container_obj.reload()
                    pid = container_obj.attrs['State']['Pid']
                
                if pid:
                    os.makedirs(PERF_LOG_DIR, exist_ok=True)
                    output_file = os.path.join(PERF_LOG_DIR, f"{function_name}_{container_id[:12]}.txt")
                    
                    # 使用您验证过的事件列表
                    events = (
                        'cycles,instructions,cache-misses,cycle_activity.stalls_total,'
                        'idq_uops_not_delivered.core,cpu-clock,mem_load_retired.l3_hit,'
                        'mem_load_retired.l3_miss,cycle_activity.stalls_l3_miss,'
                        'memory_activity.stalls_l2_miss,mem_load_retired.l1_miss,'
                        'mem_load_retired.l2_miss,mem_inst_retired.stlb_miss_loads,'
                        'mem_load_l3_miss_retired.local_dram,mem_load_l3_hit_retired.xsnp_fwd'
                    )
                    
                    perf_cmd = [
                        'sudo', 'perf', 'stat',
                        '-p', str(pid),
                        '-e', events,
                        'sleep', '300' 
                    ]
                    
                    print(f"[_dispatch_request] 启动 Perf: {' '.join(perf_cmd)}")
                    perf_log_file = open(output_file, 'w')
                    perf_process = subprocess.Popen(
                        perf_cmd, 
                        stdout=subprocess.PIPE, 
                        stderr=perf_log_file, # 将 perf 报告直接写入文件
                        preexec_fn=os.setsid 
                    )
                    
            except Exception as e:
                print(f"[_dispatch_request] 警告: 启动 perf 失败 (将继续执行): {e}")
                if 'perf_log_file' in locals() and perf_log_file:
                    perf_log_file.close()

        # --- 3. 运行 RUN (现在 perf 正在运行) ---
        print(f"[_dispatch_request] 正在转发 run 到 http://127.0.0.1:{host_port}/run")
        r = requests.post(f"http://127.0.0.1:{host_port}/run", json=payload, timeout=300)
        r.raise_for_status()
        
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        
        return data.get("result"), container_id
    
    except Exception as e:
        print(f"[_dispatch_request] 调用容器 {container_id[:12]} 时出错: {e}")
        # (日志抓取代码 保持不变)
        try:
            print(f"--- 正在抓取容器 {container_id[:12]} 的日志 ---")
            with manager.lock:
                if container_id in manager.containers:
                    logs = manager.containers[container_id]["container_obj"].logs(tail=50).decode('utf-8', errors='ignore')
                    print(logs)
            print(f"--- 容器日志结束 ---")
        except Exception as log_e:
            print(f"[_dispatch_request] 尝试获取日志时出错: {log_e}")
        
        raise e
        
    finally:
        # --- 4. 停止 PERF (不变) ---
        if run_perf and perf_process:
            print(f"[_dispatch_request] 正在向 perf 进程组发送 SIGINT...")
            try:
                os.killpg(os.getpgid(perf_process.pid), signal.SIGINT)
            except ProcessLookupError:
                pass 
            
            try:
                perf_process.communicate(timeout=5)
                print(f"[_dispatch_request] Perf 已停止。指标 已保存到 {output_file}")
            except subprocess.TimeoutExpired:
                perf_process.kill()
                print(f"[_dispatch_request] Perf 强制终止。")
            
            if 'perf_log_file' in locals() and perf_log_file:
                perf_log_file.close()
        
        # --- 5. 释放容器 (不变) ---
        print(f"[_dispatch_request] 正在释放容器 {container_id[:12]}")
        manager.release_container(container_id)
        
# --- 重构：更新 /dispatch 接口 ---
@app.route('/dispatch/<function_name>', methods=['POST']) #
def dispatch(function_name):
    """
    分发*单个*用户请求到函数管理器。
    (现在这个接口使用 _dispatch_request 辅助函数)
    """
    payload = request.get_json(silent=True) or {} #
    
    try:
        result_data, container_id = _dispatch_request(function_name, payload)
        
        # 重新组装原始的成功响应
        response_data = {
            "status": "success", 
            "result": result_data, 
            "container": container_id[:12] #
        }
        return jsonify(response_data), 200

    except Exception as e:
        print(f"[dispatch_route] 调度时出错: {e}")
        data = {"status": "error", "message": str(e)} #
        status_code = 502 #
        return jsonify(data), status_code


# --- 新增：硬编码的 Video 工作流逻辑 ---
def _run_video_workflow(payload):
    """
    在后台线程中运行的实际工作流逻辑。
    这基本就是 run_workflow_with_controller.py 的逻辑。
    """
    print("[video_workflow] 视频工作流已启动...")
    try:
        # --- 1. 获取工作流输入 ---
        video_name = payload.get("video_name")
        segment_time = payload.get("segment_time", 10)
        target_type = payload.get("target_type", "avi")
        output_prefix = payload.get("output_prefix", "final_video")

        if not video_name:
            print("[video_workflow] 错误: payload 中缺少 video_name。")
            return

        # --- 2. 调度 Split ---
        print("[video_workflow] 正在调度 SPLIT...")
        split_payload = {"video_name": video_name, "segment_time": segment_time}
        split_result, _ = _dispatch_request("video_split", split_payload)
        split_keys = split_result['split_keys']
        print(f"[video_workflow] SPLIT 完成。创建了 {len(split_keys)} 个分片。")

        # --- 3. 调度 Transcode (并行) ---
        print("[video_workflow] 正在调度 TRANSCODE (并行)...")

        def _transcode_task(split_file):
            # 这是在线程池中运行的函数
            print(f"[video_workflow]  > 开始转码: {split_file}")
            task_payload = {'split_file': split_file, 'target_type': target_type}
            result, _ = _dispatch_request("video_transcode", task_payload)
            print(f"[video_workflow]  > 完成转码: {split_file}")
            return result['transcoded_file']

        transcoded_files = []
        with ThreadPoolExecutor(max_workers=len(split_keys)) as executor: #
            transcoded_files = list(executor.map(_transcode_task, split_keys)) #
        
        print("[video_workflow] TRANSCODE 完成。")

        # --- 4. 调度 Merge ---
        print("[video_workflow] 正在调度 MERGE...")
        merge_payload = { #
            'transcoded_files': transcoded_files,
            'target_type': target_type,
            'output_prefix': output_prefix,
            'video_name': video_name
        }
        merge_result, _ = _dispatch_request("video_merge", merge_payload)
        final_video = merge_result['final_video']
        print("[video_workflow] MERGE 完成。")

        print(f"\n[video_workflow] --- 成功! ---")
        print(f"[video_workflow] 最终文件位于: {final_video}\n")

    except Exception as e:
        print(f"\n[video_workflow] --- 失败! ---")
        print(f"[video_workflow] 工作流执行出错: {e}\n")

# --- 新增：硬编码的 Recognizer 工作流逻辑 ---
def _run_recognizer_workflow(payload):
    """
    在后台线程中运行的图像审查工作流。
    """
    print("[recognizer_workflow] 图像审查工作流已启动...")
    try:
        # --- 1. 获取工作流输入 ---
        # 假设 payload 包含 {"image_filename": "my_test_image.png"}
        image_filename = payload.get("image_filename")
        if not image_filename:
            print("[recognizer_workflow] 错误: payload 中缺少 image_filename。")
            return

        # --- 2. 触发 "upload" (它只返回路径) ---
        print("[recognizer_workflow] 正在调度 UPLOAD (获取路径)...")
        upload_payload = {"image_filename": image_filename}
        upload_result, _ = _dispatch_request("recognizer_upload", upload_payload)
        image_path = upload_result['image_path']
        print(f"[recognizer_workflow] UPLOAD 完成。图像位于 {image_path}")

        # --- 3. 并行分析 (图像 + 提取) ---
        print("[recognizer_workflow] 正在调度并行分析 (Adult, Violence, Extract)...")
        
        # 我们需要使用线程池来并行执行 _dispatch_request
        # 我们将同时运行 adult, violence, 和 extract
        
        analysis_results = {}
        text_from_extract = ""

        with ThreadPoolExecutor(max_workers=3) as executor:
            # 提交任务
            future_adult = executor.submit(_dispatch_request, "recognizer_adult", {"image_path": image_path})
            future_violence = executor.submit(_dispatch_request, "recognizer_violence", {"image_path": image_path})
            future_extract = executor.submit(_dispatch_request, "recognizer_extract", {"image_path": image_path})

            # 获取结果
            # .result() 会阻塞，直到该任务完成
            
            # (注意: _dispatch_request 返回 (result_payload, container_id))
            analysis_results["adult"] = future_adult.result()[0]
            analysis_results["violence"] = future_violence.result()[0]
            
            extract_result = future_extract.result()[0]
            analysis_results["extract"] = extract_result
            text_from_extract = extract_result.get("text", "")

        print(f"[recognizer_workflow] 图像分析完成。")
        print(f"[recognizer_workflow] > Adult: {analysis_results['adult']}")
        print(f"[recognizer_workflow] > Violence: {analysis_results['violence']}")

        # --- 4. 并行文本分析 (Censor + Translate) ---
        print("[recognizer_workflow] 正在调度并行文本分析 (Censor, Translate)...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_censor = executor.submit(_dispatch_request, "recognizer_censor", {"text": text_from_extract})
            future_translate = executor.submit(_dispatch_request, "recognizer_translate", {"text": text_from_extract})

            analysis_results["censor"] = future_censor.result()[0]
            analysis_results["translate"] = future_translate.result()[0]

        print(f"[recognizer_workflow] 文本分析完成。")
        print(f"[recognizer_workflow] > Censor: {analysis_results['censor']}")
        print(f"[recognizer_workflow] > Translate: {analysis_results['translate']['translated_text']}")

        # --- 5. 决策 (在 Controller 中) ---
        is_illegal_adult = analysis_results["adult"].get("illegal", False)
        is_illegal_violence = analysis_results["violence"].get("illegal", False)
        is_illegal_censor = analysis_results["censor"].get("illegal", False)
        
        final_illegal_flag = is_illegal_adult or is_illegal_violence or is_illegal_censor
        
        print(f"[recognizer_workflow] 决策: Adult={is_illegal_adult}, Violence={is_illegal_violence}, Censor={is_illegal_censor} -> FinalDecision={final_illegal_flag}")

        final_image_path = image_path # 默认是原始图像

        # --- 6. 处理 (如果需要) ---
        if final_illegal_flag:
            print("[recognizer_workflow] 图像非法。正在调度 MOSAIC...")
            mosaic_result, _ = _dispatch_request("recognizer_mosaic", {"image_path": image_path})
            final_image_path = mosaic_result.get("mosaic_image_path")
            print(f"[recognizer_workflow] MOSAIC 完成。处理后的图像位于 {final_image_path}")
        else:
            print("[recognizer_workflow] 图像安全。跳过 MOSAIC。")

        # --- 7. 最终输出 ---
        final_result = {
            "illegal": final_illegal_flag,
            "final_image_path": final_image_path,
            "translated_text": analysis_results["translate"].get("translated_text"),
            "details": {
                "adult_check": analysis_results["adult"],
                "violence_check": analysis_results["violence"],
                "censor_check": analysis_results["censor"],
            }
        }
        
        print(f"\n[recognizer_workflow] --- 成功! ---")
        print(f"[recognizer_workflow] 最终结果: {json.dumps(final_result, indent=2)}\n")

    except Exception as e:
        print(f"\n[recognizer_workflow] --- 失败! ---")
        print(f"[recognizer_workflow] 工作流执行出错: {e}\n")

# --- 新增：硬编码的 SVD 工作流逻辑 ---
def _run_svd_workflow(payload):
    """
    在后台线程中运行的 SVD 分治工作流。
    """
    print("[svd_workflow] SVD 工作流已启动...")
    try:
        # --- 1. 获取工作流输入 ---
        # payload 示例: {"row_num": 2000, "col_num": 100, "slice_num": 2}
        row_num = payload.get("row_num", 2000)
        col_num = payload.get("col_num", 100)
        slice_num = payload.get("slice_num", 2)
        
        # --- 2. 调度 SVD Start (分割) ---
        print("[svd_workflow] 正在调度 SVD_START (分割)...")
        start_payload = {
            "row_num": row_num,
            "col_num": col_num,
            "slice_num": slice_num
        }
        start_result, _ = _dispatch_request("svd_start", start_payload)
        slice_paths = start_result['slice_paths'] # [ {"slice_paths": ["/storage/...", ...]} ]
        print(f"[svd_workflow] SVD_START 完成。创建了 {len(slice_paths)} 个切片。")

        # --- 3. 调度 SVD Compute (并行) ---
        print("[svd_workflow] 正在调度 SVD_COMPUTE (并行)...")
        
        def _compute_task(task_input):
            # task_input 是一个 (index, path) 元组
            mat_index, slice_path = task_input
            print(f"[svd_workflow]  > 开始计算: {slice_path}")
            task_payload = {
                'slice_path': slice_path,
                'mat_index': mat_index
            }
            # _dispatch_request 返回 (result_payload, container_id)
            result, _ = _dispatch_request("svd_compute", task_payload)
            print(f"[svd_workflow]  > 完成计算: {slice_path}")
            return result # 返回包含 {u_path, s_path, ...} 的 dict

        # 创建一个任务列表，包含索引和路径
        # e.g., [(0, '/storage/.../slice_0.npy'), (1, '/storage/.../slice_1.npy')]
        compute_tasks = list(enumerate(slice_paths))
        
        compute_results = []
        with ThreadPoolExecutor(max_workers=len(compute_tasks)) as executor:
            compute_results = list(executor.map(_compute_task, compute_tasks))
        
        print("[svd_workflow] SVD_COMPUTE 完成。")
        
        # --- 4. 调度 SVD Merge (合并) ---
        print("[svd_workflow] 正在调度 SVD_MERGE...")
        merge_payload = {
            'results': compute_results
        }
        merge_result, _ = _dispatch_request("svd_merge", merge_payload)
        final_paths = merge_result
        print("[svd_workflow] SVD_MERGE 完成。")

        print(f"\n[svd_workflow] --- 成功! ---")
        print(f"[svd_workflow] 最终 SVD 结果已保存至 {final_paths.get('final_u_path')}")

    except Exception as e:
        print(f"\n[svd_workflow] --- 失败! ---")
        print(f"[svd_workflow] 工作流执行出错: {e}")

# --- 新增：硬编码的 WordCount 工作流逻辑 ---
def _run_wordcount_workflow(payload):
    """
    在后台线程中运行的 WordCount MapReduce 工作流。
    """
    print("[wordcount_workflow] WordCount 工作流已启动...")
    try:
        # --- 1. 获取工作流输入 ---
        # payload 示例: {"input_filename": "book.txt", "slice_num": 4}
        input_filename = payload.get("input_filename")
        slice_num = payload.get("slice_num", 4)
        
        if not input_filename:
            print("[wordcount_workflow] 错误: payload 中缺少 input_filename。")
            return
        
        # --- 2. 调度 WordCount Start (分割) ---
        print("[wordcount_workflow] 正在调度 WORDCOUNT_START (分割)...")
        start_payload = {
            "input_filename": input_filename,
            "slice_num": slice_num
        }
        start_result, _ = _dispatch_request("wordcount_start", start_payload)
        chunk_paths = start_result['chunk_paths'] # [ {"chunk_paths": ["/storage/...", ...]} ]
        print(f"[wordcount_workflow] WORDCOUNT_START 完成。创建了 {len(chunk_paths)} 个文本块。")

        # --- 3. 调度 WordCount Count (并行 Map) ---
        print("[wordcount_workflow] 正在调度 WORDCOUNT_COUNT (并行)...")
        
        def _count_task(chunk_path):
            print(f"[wordcount_workflow]  > 开始计数: {chunk_path}")
            task_payload = {'chunk_path': chunk_path}
            result, _ = _dispatch_request("wordcount_count", task_payload)
            print(f"[wordcount_workflow]  > 完成计数: {chunk_path}")
            return result['result_path'] # 返回部分结果JSON文件的路径

        count_results_paths = []
        with ThreadPoolExecutor(max_workers=len(chunk_paths)) as executor:
            count_results_paths = list(executor.map(_count_task, chunk_paths))
        
        print("[wordcount_workflow] WORDCOUNT_COUNT 完成。")
        
        # --- 4. 调度 WordCount Merge (Reduce) ---
        print("[wordcount_workflow] 正在调度 WORDCOUNT_MERGE...")
        merge_payload = {
            'result_paths': count_results_paths
        }
        merge_result, _ = _dispatch_request("wordcount_merge", merge_payload)
        final_word_count = merge_result['final_word_count']
        print("[wordcount_workflow] WORDCOUNT_MERGE 完成。")

        print(f"\n[wordcount_workflow] --- 成功! ---")
        # 我们只打印前 10 个和总数，以防字典太大
        top_10 = sorted(final_word_count.items(), key=lambda item: item[1], reverse=True)[:10]
        print(f"[wordcount_workflow] 最终结果: 总独特单词数 = {len(final_word_count)}")
        print(f"[wordcount_workflow] 出现次数最多的前10个单词: {top_10}")

    except Exception as e:
        print(f"\n[wordcount_workflow] --- 失败! ---")
        print(f"[wordcount_workflow] 工作流执行出错: {e}")

# --- 新增：工作流调度接口 ---
@app.route('/dispatch_workflow', methods=['POST'])
def dispatch_workflow():
    """
    根据 workflow_name 调度一个硬编码的工作流。
    在后台线程中运行，并立即返回 202 (Accepted)。
    """
    body = request.get_json(silent=True) or {}
    workflow_name = body.get("workflow_name")
    payload = body.get("payload", {}) # 实际的工作流参数

    if not workflow_name:
        return jsonify({"error": "workflow_name required"}), 400

    # 这是您导师要求的 "if name＝video" 逻辑
    if workflow_name == "video":
        # 在后台线程中运行工作流，以避免 HTTP 超时
        thread = threading.Thread(
            target=_run_video_workflow,
            args=(payload,)
        )
        thread.daemon = True # 允许应用在线程运行时退出
        thread.start()
        
        return jsonify({
            "status": "started",
            "workflow_name": "video",
            "message": "视频工作流已在后台启动。请检查控制器日志。"
        }), 202 # 202 "已接受" 是用于异步任务的标准状态码
    
    elif workflow_name == "recognizer":
        thread = threading.Thread(
            target=_run_recognizer_workflow, # <-- 调用新函数
            args=(payload,)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "status": "started",
            "workflow_name": "recognizer",
            "message": "图像审查工作流已在后台启动。请检查控制器日志。"
        }), 202
    elif workflow_name == "svd":
        thread = threading.Thread(
            target=_run_svd_workflow, # <-- 调用新函数
            args=(payload,)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "status": "started",
            "workflow_name": "svd",
            "message": "SVD 工作流已在后台启动。请检查控制器日志。"
        }), 202
    elif workflow_name == "wordcount":
        thread = threading.Thread(
            target=_run_wordcount_workflow, # <-- 调用新函数
            args=(payload,)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "status": "started",
            "workflow_name": "wordcount",
            "message": "WordCount 工作流已在后台启动。请检查控制器日志。"
        }), 202
    else:
        return jsonify({"error": f"未知的 workflow_name: {workflow_name}"}), 404


# --- manager_status 和
@app.route('/manager_status/<function_name>', methods=['GET']) #
def manager_status(function_name):
    # ... 您的 manager_status 函数代码保持不变 ...
    with manager_lock:
        if function_name not in function_managers:
            return jsonify({"error": "unknown function"}), 404
        m = function_managers[function_name]

    with m.lock:
        total = len(m.containers)
        idle = sum(1 for d in m.containers.values() if d["status"] == "idle")
        busy = sum(1 for d in m.containers.values() if d["status"] == "busy")
        ports = [ {"id": cid[:12], "host_port": d.get("host_port")} for cid,d in m.containers.items() ]
    return jsonify({"function": function_name, "total": total, "idle": idle, "busy": busy, "containers": ports})


# --- Global cleanup (保持不变) ---
def clean_up_all_containers_on_exit(): #
    # ... 您的 clean_up_all_containers_on_exit 函数代码保持不变 ...
    print("Application exiting. Stopping all function containers...")
    with manager_lock:
        for manager in function_managers.values():
            try:
                manager.stop_all_containers()
            except Exception as e:
                print("Error cleaning manager:", e)
    print("All containers stopped on exit.")

atexit.register(clean_up_all_containers_on_exit) #

if __name__ == '__main__':
    # ... 您的 __main__ 代码保持不变 ...
    app.run(host='0.0.0.0', port=5000, threaded=True) #