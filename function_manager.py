import docker
import time
import threading
import os
import requests

class FunctionManager:
    def __init__(self, function_name, image_name, container_port, host_port_start=8000, idle_timeout=300):
        self.function_name = function_name
        self.image_name = image_name
        self.container_port = container_port # 容器内部服务端口
        self.host_port_start = host_port_start # 主机端口起始值
        self.idle_timeout = idle_timeout # 空闲容器回收时间 (秒)
        self.docker_client = docker.from_env()
        self.containers = {}  # {container_id: {"container_obj": ..., "status": "idle/busy", "last_active": timestamp, "host_port": ...}}
        self.lock = threading.Lock() # 用于线程同步
        self.next_host_port = host_port_start # 记录下一个可用的主机端口
        self.cleaner_thread = threading.Thread(target=self._run_cleaner, daemon=True)
        self.cleaner_thread.start()
        print(f"FunctionManager for {self.function_name} initialized.")

    def _get_next_host_port(self):
        # 简单的主机端口分配策略，可以根据实际情况进行优化
        with self.lock:
            port = self.next_host_port
            self.next_host_port += 1
            # 避免端口冲突，可以检查端口是否已被占用，这里简化处理
            return port

    def _create_new_container(self):
        host_port = self._get_next_host_port()
        container_name = f"{self.function_name}-{os.urandom(4).hex()}" # 随机生成容器名后缀
        try:
            print(f"Creating new container '{container_name}' for {self.function_name} on host port {host_port}...")
            container = self.docker_client.containers.run(
                self.image_name,
                detach=True,
                ports={f"{self.container_port}/tcp": host_port},
                name=container_name
            )
            with self.lock:
                self.containers[container.id] = {
                    "container_obj": container,
                    "status": "idle", # 初始状态为idle, 之后请求会将其变为busy
                    "last_active": time.time(),
                    "host_port": host_port
                }
            print(f"Container '{container_name}' created with ID: {container.id} and host port {host_port}.")
            return container.id
        except docker.errors.ImageNotFound:
            print(f"Error: Image '{self.image_name}' not found. Please build it first.")
            return None
        except Exception as e:
            print(f"Error creating container: {e}")
            return None

    def _wait_for_container_service(self, host_port, timeout=30, check_interval=1):
        """
        轮询检查容器内的服务是否可用。
        这里假设服务的 /status 端点返回 200 OK 且 body 包含 {"status": "new"} 或 {"status": "ok"}。
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 尝试连接容器的 /status 端点
                response = requests.get(f"http://127.0.0.1:{host_port}/status", timeout=check_interval)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") in ["new", "ok"]: # 根据你的代理服务的 /status 返回值调整
                        print(f"Container service on port {host_port} is ready.")
                        return True
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # print(f"Service not ready on port {host_port}: {e}") # 可以在调试时取消注释
                pass
            time.sleep(check_interval)
        print(f"Container service on port {host_port} did not become ready within {timeout} seconds.")
        return False

    def get_container_for_request(self):
        with self.lock:
            # 寻找空闲容器
            for container_id, data in self.containers.items():
                if data["status"] == "idle" and data["container_obj"].status == 'running':
                    data["status"] = "busy"
                    data["last_active"] = time.time()
                    print(f"Assigned existing idle container {container_id[:12]} for {self.function_name}.")
                    return data["host_port"], container_id

        # 如果没有空闲容器，则创建一个新容器
        new_container_id = self._create_new_container()
        if new_container_id:
            with self.lock:
                container_data = self.containers[new_container_id]
            
            host_port = container_data["host_port"]
            if not self._wait_for_container_service(host_port):
                print(f"Failed to bring up service for container {new_container_id[:12]} on port {host_port}. Removing it.")
                self._remove_container(new_container_id, container_data["container_obj"])
                return None, None # 健康检查失败，不分配此容器

            with self.lock:
                container_data["status"] = "busy"
                container_data["last_active"] = time.time()
                print(f"Assigned newly created container {new_container_id[:12]} for {self.function_name}.")
                return host_port, new_container_id
        return None, None

    def release_container(self, container_id):
        with self.lock:
            if container_id in self.containers:
                self.containers[container_id]["status"] = "idle"
                self.containers[container_id]["last_active"] = time.time()
                print(f"Container {container_id[:12]} for {self.function_name} released and set to idle.")

    def _remove_container(self, container_id, container_obj):
        try:
            print(f"Stopping and removing container {container_id[:12]} (name: {container_obj.name}) for {self.function_name}...")
            container_obj.stop()
            container_obj.remove()
            with self.lock:
                if container_id in self.containers:
                    del self.containers[container_id]
            print(f"Container {container_id[:12]} removed.")
        except Exception as e:
            print(f"Error removing container {container_id[:12]}: {e}")

    def _run_cleaner(self):
        while True:
            time.sleep(30) # 每30秒检查一次
            print(f"Running cleaner for {self.function_name}. Current active containers: {len(self.containers)}")
            containers_to_remove = []
            current_time = time.time()
            with self.lock:
                for container_id, data in self.containers.items():
                    # 只有空闲且长时间未活动的容器才会被回收
                    if data["status"] == "idle" and (current_time - data["last_active"]) > self.idle_timeout:
                        containers_to_remove.append((container_id, data["container_obj"]))
            
            for container_id, container_obj in containers_to_remove:
                self._remove_container(container_id, container_obj)
            
            # 确保至少有一个空闲容器，或者在没有请求时允许全部回收
            # 如果希望一直保持一个预热容器，可以在这里添加逻辑
            # current_idle_count = sum(1 for data in self.containers.values() if data["status"] == "idle")
            # if current_idle_count == 0 and len(self.containers) > 0:
            #     self._create_new_container() # 保持一个空闲容器


    def stop_all_containers(self):
        print(f"Stopping all containers for {self.function_name}...")
        containers_to_stop = []
        with self.lock:
            for container_id, data in self.containers.items():
                containers_to_stop.append((container_id, data["container_obj"]))
        
        for container_id, container_obj in containers_to_stop:
            self._remove_container(container_id, container_obj)
        print(f"All containers for {self.function_name} stopped and removed.")
