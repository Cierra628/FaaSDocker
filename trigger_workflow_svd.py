import requests
import os
import sys

# --- 配置 ---
CONTROLLER_URL = 'http://localhost:5000'
HOST_STORAGE_PATH = '/home/jywang/FaaSDocker/storage'
IMAGE_NAME = 'video-proxy:latest'
PROXY_CONTAINER_PORT = 5000

# --- 1. 注册 SVD 相关的 Function Managers ---
def setup_svd_managers():
    print("正在向 Controller 注册 SVD Function Managers...")
    
    svd_functions = [
        {"function_name": "svd_start", "min_idle": 1},
        {"function_name": "svd_compute", "min_idle": 2}, # 预热 2 个
        {"function_name": "svd_merge", "min_idle": 1}
    ]
    
    for func in svd_functions:
        config = {
            "function_name": func["function_name"],
            "image_name": IMAGE_NAME,
            "container_port": PROXY_CONTAINER_PORT,
            "min_idle_containers": func.get("min_idle", 0),
            "host_storage_path": HOST_STORAGE_PATH # SVD 需要共享存储
        }
        try:
            resp = requests.post(f"{CONTROLLER_URL}/create_manager", json=config)
            resp.raise_for_status()
            print(f"  > Manager '{func['function_name']}' 已创建/已存在。")
        except requests.RequestException as e:
            if e.response and e.response.status_code != 200: # 忽略 200 (exists)
                print(f"  > 创建 manager '{func['function_name']}' 失败: {e}")
                sys.exit(1)

# --- 2. 清理旧的输出目录 ---
def prepare_storage():
    print("正在清理 SVD 存储目录...")
    for subdir in ['svd_input', 'svd_output', 'svd_final']:
        full_path = os.path.join(HOST_STORAGE_PATH, subdir)
        os.system(f'rm -rf {full_path}')
        os.makedirs(full_path, exist_ok=True)

# --- 3. 触发工作流 ---
def trigger_workflow():
    print(f"\n正在 {CONTROLLER_URL} 上触发 'svd' 工作流...")
    
    # 定义要计算的矩阵大小和切片数
    workflow_payload = {
        "row_num": 2000,
        "col_num": 100,
        "slice_num": 2  # 将切分成 2 片
    }
    
    try:
        resp = requests.post(
            f"{CONTROLLER_URL}/dispatch_workflow",
            json={
                "workflow_name": "svd",
                "payload": workflow_payload
            }
        )
        resp.raise_for_status()
        
        print("\n--- 来自 Controller 的响应 ---")
        print(f"状态码: {resp.status_code}")
        print(f"响应体: {resp.json()}")
        
        if resp.status_code == 202:
            print("\n工作流已在后台启动。")
            print("请在运行 controller.py 的终端中查看实时日志！")
        
    except requests.RequestException as e:
        print(f"\n--- 触发工作流时出错 ---")
        print(f"错误: {e}")
        if e.response:
            print(f"响应体: {e.response.text}")

if __name__ == '__main__':
    setup_svd_managers()
    prepare_storage()
    trigger_workflow()