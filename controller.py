# controller.py
from flask import Flask, request, jsonify
import threading
from function_manager import FunctionManager #
import atexit
import time
import requests  # <-- 需要导入 requests
from concurrent.futures import ThreadPoolExecutor # <-- 新增导入

app = Flask(__name__) #

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

# --- 新增：可重用的内部 Dispatch 函数 ---
def _dispatch_request(function_name, payload):
    """
    内部共享逻辑：为函数获取、初始化、运行并释放一个容器。
    返回: (result_payload, container_id)
    会抛出异常如果失败。
    """
    print(f"[_dispatch_request] 正在为 '{function_name}' 寻找 manager...")
    with manager_lock:
        if function_name not in function_managers: #
            print(f"[_dispatch_request] 错误: 未知的函数 {function_name}")
            raise Exception(f"未知的函数: {function_name}")
        manager = function_managers[function_name] #

    print(f"[_dispatch_request] 正在为 '{function_name}' 获取容器...")
    host_port, container_id = manager.get_container_for_request() #
    if not host_port: #
        print(f"[_dispatch_request] 错误: 无法获取容器 {function_name}")
        raise Exception(f"无法获取容器 {function_name}")

    try:
        # 1. 调用 /init (来自原始 /dispatch 的逻辑)
        try:
            init_data = {"action": function_name} #
            manager_url = f"http://127.0.0.1:{host_port}" #
            print(f"[_dispatch_request] 正在为 {container_id[:12]} 调用 {manager_url}/init")
            requests.post(f"{manager_url}/init", json=init_data, timeout=10) #
        except Exception as e:
            # 非致命错误，继续尝试 /run
            print(f"[_dispatch_request] init 错误 (非致命): {e}")

        # 2. 调用 /run (来自原始 /dispatch 的逻辑)
        print(f"[_dispatch_request] 正在转发 run 到 http://127.0.0.1:{host_port}/run")
        r = requests.post(f"http://127.0.0.1:{host_port}/run", json=payload, timeout=60) #
        r.raise_for_status() # 抛出 4xx/5xx 错误
        
        try:
            data = r.json() #
        except Exception:
            data = {"raw": r.text} #
        
        # 假设 proxy.py 总是返回一个包含 "result" 的字典
        return data.get("result"), container_id
    
    except Exception as e:
        print(f"[_dispatch_request] 调用容器 {container_id[:12]} 时出错: {e}")
        raise e # 重新抛出异常，让上层(工作流)知道失败了
    finally:
        # 3. 释放容器 (来自原始 /dispatch 的 finally 块)
        print(f"[_dispatch_request] 正在释放容器 {container_id[:12]}")
        manager.release_container(container_id) #


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
        split_result, _ = _dispatch_request("split", split_payload)
        split_keys = split_result['split_keys']
        print(f"[video_workflow] SPLIT 完成。创建了 {len(split_keys)} 个分片。")

        # --- 3. 调度 Transcode (并行) ---
        print("[video_workflow] 正在调度 TRANSCODE (并行)...")

        def _transcode_task(split_file):
            # 这是在线程池中运行的函数
            print(f"[video_workflow]  > 开始转码: {split_file}")
            task_payload = {'split_file': split_file, 'target_type': target_type}
            result, _ = _dispatch_request("transcode", task_payload)
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
        merge_result, _ = _dispatch_request("merge", merge_payload)
        final_video = merge_result['final_video']
        print("[video_workflow] MERGE 完成。")

        print(f"\n[video_workflow] --- 成功! ---")
        print(f"[video_workflow] 最终文件位于: {final_video}\n")

    except Exception as e:
        print(f"\n[video_workflow] --- 失败! ---")
        print(f"[video_workflow] 工作流执行出错: {e}\n")


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