import requests
import os
import sys

CONTROLLER_URL = 'http://localhost:5000' #
HOST_STORAGE_PATH = '/home/jywang/FaaSDocker/storage' #
VIDEO_FILE = 'my_video.mp4' #
VIDEO_SOURCE_PATH = os.path.join(os.path.expanduser('~'), 'FaaSDocker', 'sources', VIDEO_FILE)#
IMAGE_NAME = 'video-proxy:latest' #
PROXY_CONTAINER_PORT = 5000 #

def setup_function_managers():
    """
    我们仍然需要先注册 function managers
    (或者您也可以把这个逻辑也硬编码到 controller 里)
    """
    print("正在向 Controller 注册 Function Managers...")
    functions_to_create = [ #
        {"function_name": "split", "image_name": IMAGE_NAME, "container_port": PROXY_CONTAINER_PORT, "min_idle_containers": 1, "host_storage_path": HOST_STORAGE_PATH},
        {"function_name": "transcode", "image_name": IMAGE_NAME, "container_port": PROXY_CONTAINER_PORT, "min_idle_containers": 1, "host_storage_path": HOST_STORAGE_PATH},
        {"function_name": "merge", "image_name": IMAGE_NAME, "container_port": PROXY_CONTAINER_PORT, "host_storage_path": HOST_STORAGE_PATH}
    ]
    
    for config in functions_to_create: #
        try:
            resp = requests.post(f"{CONTROLLER_URL}/create_manager", json=config) #
            resp.raise_for_status() #
        except requests.RequestException:
            pass # 忽略已存在的错误

def copy_video_file():
    """
    确保视频文件在共享存储中
    """
    # ... (与之前相同的准备存储逻辑)
    if not os.path.exists(VIDEO_SOURCE_PATH):
        print(f"错误: 视频文件未找到 {VIDEO_SOURCE_PATH}")
        sys.exit(1)
    if not os.path.exists(HOST_STORAGE_PATH):
        print(f"错误: 存储路径未找到 {HOST_STORAGE_PATH}")
        sys.exit(1)
        
    for subdir in ['split_output', 'transcode_output', 'merge_output']: #
        os.system(f'rm -rf {os.path.join(HOST_STORAGE_PATH, subdir)}') #
        os.makedirs(os.path.join(HOST_STORAGE_PATH, subdir), exist_ok=True) #
    
    os.system(f'cp {VIDEO_SOURCE_PATH} {os.path.join(HOST_STORAGE_PATH, VIDEO_FILE)}') #
    print(f"视频 '{VIDEO_FILE}' 已复制到共享存储。")


def trigger_workflow():
    print(f"正在 {CONTROLLER_URL} 上触发 'video' 工作流...")
    
    # 这是工作流需要的参数
    workflow_payload = { #
        "video_name": VIDEO_FILE,
        "segment_time": 10,
        "target_type": "avi",
        "output_prefix": "workflow_final"
    }
    
    try:
        resp = requests.post(
            f"{CONTROLLER_URL}/dispatch_workflow", # <-- 调用新接口
            json={
                "workflow_name": "video", # <-- "if name=video"
                "payload": workflow_payload # <-- 传递参数
            }
        )
        resp.raise_for_status()
        
        print("\n--- 来自 Controller 的响应 ---")
        print(f"状态码: {resp.status_code}") #
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
    setup_function_managers()
    copy_video_file()
    trigger_workflow()