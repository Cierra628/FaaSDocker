from flask import Flask, request, jsonify
import threading
from function_manager import FunctionManager
import time
import requests 

app = Flask(__name__)

function_managers = {}
manager_lock = threading.Lock()

DEFAULT_IMAGE_NAME = "myimage:latest" # 确保与镜像名匹配
DEFAULT_CONTAINER_PORT = 5000
DEFAULT_HOST_PORT_START = 8000
DEFAULT_IDLE_TIMEOUT = 15 # 测试时可以设置短一点

@app.route('/<function_name>/invoke', methods=['POST'])
def invoke_function(function_name):
    with manager_lock:
        if function_name not in function_managers:
            print(f"Creating new FunctionManager for {function_name}...")
            function_managers[function_name] = FunctionManager(
                function_name=function_name,
                image_name=DEFAULT_IMAGE_NAME,
                container_port=DEFAULT_CONTAINER_PORT,
                host_port_start=DEFAULT_HOST_PORT_START + len(function_managers) * 100,
                idle_timeout=DEFAULT_IDLE_TIMEOUT
            )
            print(f"FunctionManager for {function_name} created.")

    manager = function_managers[function_name]

    host_port, container_id = manager.get_container_for_request()

    if not host_port:
        return jsonify({"status": "error", "message": "Failed to get a container for the request."}), 500

    try:
        init_data = {"action": function_name}
        print(f"Sending init request to http://localhost:{host_port}/init with action: {function_name}")
        # 使用 requests 库发送真正的 HTTP 请求到容器内部的服务
        # 这里是模拟你的代理服务的 /init 和 /run 端点
        # 假设 /init 成功返回 {"status": "ok"}
        requests.post(f"http://127.0.0.1:{host_port}/init", json=init_data, timeout=10)


        request_data = request.get_json()
        print(f"Sending run request to http://localhost:{host_port}/run with data: {request_data}")
        # 假设 /run 成功返回 {"duration": ..., "start_time": ..., "end_time": ...}
        response_from_container = requests.post(f"http://127.0.0.1:{host_port}/run", json=request_data, timeout=10)
        response_data = response_from_container.json()
        response_data["status"] = "success"
        response_data["message"] = f"Function {function_name} invoked on container {container_id[:12]}"
        status_code = 200

    except requests.exceptions.ConnectionError as e:
        print(f"Connection error to container {container_id[:12]} on port {host_port}: {e}")
        response_data = {"status": "error", "message": f"Container connection error: {str(e)}"}
        status_code = 502 # Bad Gateway
    except requests.exceptions.Timeout:
        print(f"Request to container {container_id[:12]} on port {host_port} timed out.")
        response_data = {"status": "error", "message": "Request to container timed out."}
        status_code = 504 # Gateway Timeout
    except Exception as e:
        print(f"Error invoking function {function_name} on container {container_id[:12]}: {e}")
        response_data = {"status": "error", "message": f"Error during function invocation: {str(e)}"}
        status_code = 500
    finally:
        manager.release_container(container_id)

    return jsonify(response_data), status_code

# 我想这一段触发逻辑或许有问题
@app.teardown_appcontext
def shutdown_app(exception=None):
    print("Shutting down Controller. Stopping all function containers...")
    with manager_lock:
        for manager in function_managers.values():
            manager.stop_all_containers()
    print("All containers stopped.")


if __name__ == '__main__':
    # 启动 Flask 应用的线程
    def run_flask_app():
        app.run(host='0.0.0.0', port=5000, threaded=True) # 开启多线程处理请求

    flask_thread = threading.Thread(target=run_flask_app, daemon=True) # 设置为守护线程
    flask_thread.start()
    print("Flask app started in a separate thread.")

    # 等待 Flask 应用完全启动 (可能需要几秒)
    time.sleep(3) 

    # 预先为 'matmul' 函数创建管理器 (在 Flask 线程之外，因为 Manager 的初始化可能涉及 Docker API 调用)
    with manager_lock:
        matmul_manager = FunctionManager(
            function_name="matmul",
            image_name=DEFAULT_IMAGE_NAME,
            container_port=DEFAULT_CONTAINER_PORT,
            host_port_start=DEFAULT_HOST_PORT_START,
            idle_timeout=DEFAULT_IDLE_TIMEOUT
        )
        function_managers["matmul"] = matmul_manager
        print("Pre-created FunctionManager for 'matmul'.")

    print("\n--- Manually creating two initial containers for matmul ---")
    # 发送两个真实的请求来创建和初始化容器，并让它们释放
    # 这确保容器是真实地创建和可用的
    
    results = []
    for i in range(1, 3): # 发送两个请求来预热两个容器
        print(f"Pre-warming request {i} for matmul...")
        try:
            response = requests.post(f"http://127.0.0.1:5000/matmul/invoke", json={"param": 1000 + i})
            print(f"Pre-warming request {i} response: {response.status_code} - {response.json()}")
            results.append(response.json())
        except Exception as e:
            print(f"Pre-warming request {i} failed: {e}")
    
    # 简单检查，确保两个容器确实被创建并已释放
    # 注意：这里的检查可能不直接反映 "idle" 状态，但至少它们被创建了
    # 你可以通过查看 matmul_manager.containers 字典的内容来确认
    with matmul_manager.lock:
        print(f"After pre-warming, matmul has {len(matmul_manager.containers)} containers.")
    time.sleep(1) # 稍微等待一下，确保容器状态更新为idle


    print(f"\n--- Initial setup done. Now func 'matmul' has {len(matmul_manager.containers)} containers, both idle. ---")
    print("--- Simulating 3 concurrent requests for 'matmul' ---")

    def send_request(request_num):
        print(f"Sending Request {request_num} for matmul...")
        try:
            response = requests.post(f"http://127.0.0.1:5000/matmul/invoke", json={"param": 1000 + request_num})
            print(f"Request {request_num} response: {response.status_code} - {response.json()}")
        except Exception as e:
            print(f"Request {request_num} failed: {e}")

    threads = []
    for i in range(1, 4): # 3个请求
        thread = threading.Thread(target=send_request, args=(i,))
        threads.append(thread)
        thread.start()
        time.sleep(0.1) # 稍微间隔一下，避免所有请求同时到达

    for thread in threads:
        thread.join() # 等待所有请求线程完成

    print("\n--- All 3 requests simulated ---")
    
    # 确保 matmul_manager 有足够时间更新其内部状态
    time.sleep(1)
    with matmul_manager.lock:
        print(f"Current containers for matmul after all requests: {len(matmul_manager.containers)}")

    print("\n--- Waiting for idle container cleanup ---")
    time.sleep(matmul_manager.idle_timeout + 5) # 等待回收机制触发

    # 清理所有容器
    matmul_manager.stop_all_containers()
    print("Example finished.")

    # 注意：由于 Flask 应用在守护线程中运行，主线程退出时它也会退出。
    # 如果你希望 Flask 应用持续运行以接受外部请求，需要调整这部分逻辑。