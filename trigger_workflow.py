import requests
import os
import sys
import json

# --- 1. 全局配置 (不变) ---
CONTROLLER_URL = 'http://localhost:5000'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST_STORAGE_PATH = os.path.join(BASE_DIR, "storage")
HOST_SOURCE_DIR = os.path.join(BASE_DIR, "sources")
IMAGE_NAME = 'workflow-proxy:latest'
PROXY_CONTAINER_PORT = 5000

# --- 2. (新) 目标性的 Manager 注册函数 ---
def setup_managers_for(workflow_name):
    """
    根据 workflow_name，只注册该工作流 所需的 managers。
    """
    print(f"正在为 '{workflow_name}' 注册 Managers...")
    
    # 定义所有工作流 的 Manager 需求
    all_managers = {
        "video": [
            {"name": "video_split", "min_idle": 1},
            {"name": "video_transcode", "min_idle": 1},
            {"name": "video_merge", "min_idle": 0},
        ],
        "recognizer": [
            {"name": "recognizer_upload", "min_idle": 1},
            {"name": "recognizer_extract", "min_idle": 1},
            {"name": "recognizer_adult", "min_idle": 1},
            {"name": "recognizer_violence", "min_idle": 1},
            {"name": "recognizer_censor", "min_idle": 1},
            {"name": "recognizer_translate", "min_idle": 1},
            {"name": "recognizer_mosaic", "min_idle": 0},
        ],
        "svd": [
            {"name": "svd_start", "min_idle": 1},
            {"name": "svd_compute", "min_idle": 2},
            {"name": "svd_merge", "min_idle": 1},
        ],
        "wordcount": [
            {"name": "wordcount_start", "min_idle": 1},
            {"name": "wordcount_count", "min_idle": 2},
            {"name": "wordcount_merge", "min_idle": 1},
        ]
    }
    
    # 获取当前工作流 需要的 managers
    managers_to_register = all_managers.get(workflow_name)
    if not managers_to_register:
        print(f"错误: 无法为 '{workflow_name}' 找到 managers 定义。")
        sys.exit(1)

    # 循环注册
    for func in managers_to_register:
        config = {
            "function_name": func["name"],
            "image_name": IMAGE_NAME,
            "container_port": PROXY_CONTAINER_PORT,
            "min_idle_containers": func.get("min_idle", 0),
        }
        
        if func.get("needs_storage", True):
            config["host_storage_path"] = HOST_STORAGE_PATH

        try:
            resp = requests.post(f"{CONTROLLER_URL}/create_manager", json=config)
            resp.raise_for_status()
            print(f"  > Manager '{func['name']}' 已创建/已存在。")
        except requests.RequestException as e:
            if e.response and e.response.status_code != 200: # 忽略 "exists"
                print(f"  > 创建 manager '{func['name']}' 失败: {e}")
                sys.exit(1)
                
    print(f"'{workflow_name}' 的 Managers 均已注册。")


# --- 3. (新) 目标性的存储准备函数 ---
def prepare_storage_for(workflow_name):
    """
    根据 workflow_name，只准备该工作流 所需的文件和目录。
    """
    print(f"正在为 '{workflow_name}' 准备存储...")
    
    # 确保基础目录存在
    os.makedirs(HOST_SOURCE_DIR, exist_ok=True)
    storage_source_dir = os.path.join(HOST_STORAGE_PATH, 'sources')
    os.makedirs(storage_source_dir, exist_ok=True)

    # 1. 定义源文件需求
    source_file_map = {
        "video": "my_video.mp4",
        "recognizer": "test.png",
        "wordcount": "book.txt"
        # "svd" 不需要源文件
    }
    
    # 2. 定义输出目录需求
    output_dir_map = {
        "video": ['output/video_split', 'output/video_transcode', 'output/video_merge'],
        "recognizer": ['output/recognizer_mosaic'],
        "svd": ['output/svd_start', 'output/svd_compute', 'output/svd_merge'],
        "wordcount": ['output/wordcount_start', 'output/wordcount_count', 'output/wordcount_merge']
    }
    # (新) 总是创建和清理 perf_logs 目录
    perf_log_dir = os.path.join(HOST_STORAGE_PATH, 'perf_logs')
    os.makedirs(perf_log_dir, exist_ok=True)
    
    # 3. 准备源文件 (如果需要)
    filename = source_file_map.get(workflow_name)
    if filename:
        host_path = os.path.join(HOST_SOURCE_DIR, filename)
        storage_path = os.path.join(storage_source_dir, filename)
        
        if not os.path.exists(host_path):
            print(f"!! 警告: '{workflow_name}' 的源文件 {host_path} 未找到！")
            print(f"!! 请确保在运行 '{workflow_name}' 工作流之前放置该文件。")
            sys.exit(1)
        
        # 复制到共享工作区
        os.system(f'cp {host_path} {storage_path}')
        print(f"已将源文件 '{filename}' 同步到 {storage_source_dir}")

    # 4. 清理输出目录 (如果需要)
    dirs_to_clean = output_dir_map.get(workflow_name)
    if dirs_to_clean:
        print(f"正在清理 '{workflow_name}' 的旧输出目录...")
        for subdir in dirs_to_clean:
            full_path = os.path.join(HOST_STORAGE_PATH, subdir)
            os.system(f'rm -rf {full_path}')
            os.makedirs(full_path, exist_ok=True)
    
    print(f"'{workflow_name}' 的存储准备完毕。")


# --- 4. 统一的工作流触发函数 (不变) ---
def trigger_workflow(workflow_name):
    """
    根据 workflow_name 构建 payload 并发送请求。
    """
    print(f"\n--- 正在触发: '{workflow_name}' ---")
    
    payload = {}
    
    # 1. 根据名称构建 Payload
    if workflow_name == "video":
        payload = {
            "video_name": "my_video.mp4",
            "target_type": "avi",
            "output_prefix": "final_video"
        }
    elif workflow_name == "recognizer":
        payload = {
            "image_filename": "test.png"
        }
    elif workflow_name == "svd":
        payload = {
            "row_num": 2000,
            "col_num": 100,
            "slice_num": 2
        }
    elif workflow_name == "wordcount":
        payload = {
            "input_filename": "book.txt",
            "slice_num": 4
        }
    elif workflow_name == "matmul":
        payload = {
            "param": 4000
        }
    else:
        # 这个检查在 main 入口已经做过了，但作为双重保险
        print(f"错误: 未知的工作流名称: '{workflow_name}'")
        return

    # 2. 决定调用哪个 API 接口
    if workflow_name == "matmul":
        api_url = f"{CONTROLLER_URL}/dispatch/{workflow_name}"
    else:
        api_url = f"{CONTROLLER_URL}/dispatch_workflow"
        payload = {
            "workflow_name": workflow_name,
            "payload": payload
        }
        
    # 3. 发送请求
    try:
        resp = requests.post(api_url, json=payload)
        resp.raise_for_status()
        
        print("\n--- 来自 Controller 的响应 ---")
        print(f"状态码: {resp.status_code}")
        print(f"响应体: {json.dumps(resp.json(), indent=2)}")
        
        if resp.status_code == 202:
            print("\n工作流已在后台启动。请在 controller.py 日志中查看进度！")
        elif resp.status_code == 200:
            print("\n简单 Action 执行完毕。")
        
    except requests.RequestException as e:
        print(f"\n--- 触发工作流时出错 ---")
        print(f"错误: {e}")
        if e.response:
            print(f"响应体: {e.response.text}")

# --- 5. (新) 主执行入口 ---
if __name__ == "__main__":
    
    # 1. 从命令行读取要运行的工作流名称
    if len(sys.argv) != 2:
        print("错误: 请提供一个要运行的工作流名称。")
        print("用法: python3 trigger.py [video|recognizer|svd|wordcount|matmul]")
        sys.exit(1)
        
    workflow_to_run = sys.argv[1].lower()
    
    # 检查工作流名称是否有效
    valid_workflows = ["video", "recognizer", "svd", "wordcount", "matmul"]
    if workflow_to_run not in valid_workflows:
        print(f"错误: '{workflow_to_run}' 不是一个有效的工作流名称。")
        print("有效名称: [video|recognizer|svd|wordcount|matmul]")
        sys.exit(1)
    
    # 2. (新) 运行目标性的设置
    setup_managers_for(workflow_to_run)
    prepare_storage_for(workflow_to_run)
    
    # 3. 运行选定的工作流
    trigger_workflow(workflow_to_run)