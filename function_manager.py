import docker
import time
import threading
import os
import requests

class FunctionManager:
    def __init__(self, function_name, image_name, container_port, host_port_start=8000, idle_timeout=300, min_idle_containers=1):
        self.function_name = function_name
        self.image_name = image_name
        self.container_port = container_port
        self.host_port_start = host_port_start
        self.idle_timeout = idle_timeout
        self.min_idle_containers = min_idle_containers
        self.docker_client = docker.from_env()
        self.containers = {}  # {container_id: {"container_obj": ..., "status": "idle/busy", "last_active": timestamp, "host_port": ...}}
        self.lock = threading.Lock()
        self.next_host_port = host_port_start
        self._cleaner_stop_event = threading.Event()

        self.cleaner_thread = threading.Thread(target=self._run_cleaner, daemon=True)
        self.cleaner_thread.start()
        print(f"FunctionManager for {self.function_name} initialized.")

    def _get_next_host_port(self):
        with self.lock:
            port = self.next_host_port
            self.next_host_port += 1
            # TODO: Add logic to check if port is actually free
            return port

    def _wait_for_container_service(self, host_port, timeout=30, check_interval=0.01):
        """
        timeout: 总超时时间(秒)
        check_interval: 每次轮询前 sleep 的时间(秒), 此处设置为10ms
        requests timeout: 单次请求超时, 尝试设置为 0.5s
        """
        start_time = time.time()
        req_timeout = 0.5
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"http://127.0.0.1:{host_port}/status", timeout=req_timeout)
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = {}
                    if data.get("status") in ["new", "ok", "ready"]:
                        print(f"Container service on port {host_port} is ready.")
                        return True
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # 只记录少量调试信息以免太噪
                # print(f"status check: {e}")  # 可按需打开
                pass
            time.sleep(check_interval)
        print(f"Container service on port {host_port} did not become ready within {timeout} seconds.")
        return False

    def _create_new_container(self):
        # 使用 Docker 随机映射宿主端口，避免端口冲突
        container_name = f"{self.function_name}-{os.urandom(4).hex()}"
        try:
            print(f"Creating new container '{container_name}' for {self.function_name} (image={self.image_name}) ...")
            container = self.docker_client.containers.run(
                self.image_name,
                detach=True,
                ports={f"{self.container_port}/tcp": None},  # 让 Docker 自动分配宿主端口
                name=container_name
            )
            print(f"Created container id={container.id[:12]}")
        except docker.errors.ImageNotFound:
            print(f"Error: Image '{self.image_name}' not found.")
            return None
        except Exception as e:
            print(f"Error creating container '{container_name}': {e}")
            return None

        # 等待 Docker 完成端口映射
        host_port = None
        for _ in range(60):  # 最多等待约 30s（60*0.5）
            try:
                container.reload()
                mapping = container.attrs.get("NetworkSettings", {}).get("Ports", {}).get(f"{self.container_port}/tcp")
                if mapping and mapping[0].get("HostPort"):
                    host_port = int(mapping[0]["HostPort"])
                    break
            except Exception as e:
                print("container inspect exception:", e)
            time.sleep(0.5)

        if not host_port:
            print(f"Service mapping not available for container {container.id[:12]}; attrs={container.attrs}")
            try:
                print("Container logs (tail 50):")
                print(container.logs(tail=50).decode(errors='ignore'))
            except Exception:
                pass
            try:
                container.stop(timeout=1)
                container.remove(force=True)
            except Exception as e:
                print("cleanup error:", e)
            return None

        # 健康检查
        if not self._wait_for_container_service(host_port, timeout=30, check_interval=0.1):
            print(f"Service for newly created container {container.id[:12]} on port {host_port} not ready, removing it.")
            try:
                print("Container logs (tail 80):")
                print(container.logs(tail=80).decode(errors='ignore'))
            except Exception:
                pass
            try:
                container.stop(timeout=1)
                container.remove(force=True)
            except Exception as e:
                print("Error cleaning up failed new container:", e)
            return None

        with self.lock:
            self.containers[container.id] = {
                "container_obj": container,
                "status": "idle",
                "last_active": time.time(),
                "host_port": host_port
            }
        print(f"Container '{container_name}' created id={container.id[:12]} host_port={host_port}. Service ready.")
        return container.id


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
                # 确保新创建的容器也设置为busy并返回其端口
                container_data = self.containers[new_container_id]
                container_data["status"] = "busy" # 新创建的容器直接用于请求，所以是busy
                container_data["last_active"] = time.time()
                print(f"Assigned newly created container {new_container_id[:12]} for {self.function_name}.")
                return container_data["host_port"], new_container_id
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
            # 尝试停止容器，给定一个短的超时
            container_obj.stop(timeout=5)
            # 强制删除容器，即使它仍在运行或停止失败
            container_obj.remove(force=True)
            with self.lock:
                if container_id in self.containers:
                    del self.containers[container_id]
            print(f"Container {container_id[:12]} removed.")
        except docker.errors.NotFound:
            print(f"Container {container_id[:12]} not found, likely already removed.")
            with self.lock:
                if container_id in self.containers:
                    del self.containers[container_id]
        except Exception as e:
            print(f"Error removing container {container_id[:12]}: {e}. Forcing internal cleanup.")
            # 即使移除失败，也要尝试从 internal 列表中删除，避免重复尝试
            with self.lock:
                if container_id in self.containers:
                    del self.containers[container_id]

    def _run_cleaner(self):
        while not self._cleaner_stop_event.is_set():
            # 使用 wait，使线程可被快速唤醒停止
            self._cleaner_stop_event.wait(timeout=30)
            if self._cleaner_stop_event.is_set():
                break

            print(f"Running cleaner for {self.function_name}. Current active containers: {len(self.containers)}")
            containers_to_remove = []
            current_time = time.time()

            # 1) 计算哪些容器需要被移除（只在锁内做轻量操作）
            with self.lock:
                idle_running = []
                for cid, data in list(self.containers.items()):
                    # 刷新 container 状态 (非阻塞式)
                    try:
                        data["container_obj"].reload()
                    except Exception:
                        pass
                    if data["status"] == "idle" and data["container_obj"].status == 'running':
                        idle_running.append((cid, data))
                idle_running.sort(key=lambda item: item[1]["last_active"])

                # 标记那些需要移除的容器（不在这里做实际删除）
                for i, (container_id, data) in enumerate(idle_running):
                    # 保留 min_idle_containers 个最近的 idle 容器
                    num_idle_after = len(idle_running) - i
                    if num_idle_after > self.min_idle_containers and (current_time - data["last_active"]) > self.idle_timeout:
                        containers_to_remove.append((container_id, data["container_obj"]))
                    else:
                        # 剩下的都比较新或属于 min_reserved，跳出以避免多删
                        pass

            # 2) 在锁外实际删除容器（避免长时间持锁）
            for container_id, container_obj in containers_to_remove:
                # Before removing, try to fetch logs/attrs for debugging (optional)
                try:
                    print(f"[Cleaner] Removing idle container {container_id[:12]} (name={getattr(container_obj,'name',None)})")
                    # safe removal handled in _remove_container which acquires lock internally
                    self._remove_container(container_id, container_obj)
                except Exception as e:
                    print(f"[Cleaner] Error removing {container_id[:12]}: {e}")

            # 3) 检查是否需要预热新容器；计算需要创建的数量（在锁内做最小工作）
            to_create = 0
            with self.lock:
                current_idle_count = sum(
                    1 for data in self.containers.values()
                    if data["status"] == "idle" and data["container_obj"].status == 'running'
                )
                if current_idle_count < self.min_idle_containers:
                    to_create = self.min_idle_containers - current_idle_count
                    print(f"Need to create {to_create} new idle containers for pre-warming.")

            # 4) 在锁外循环创建新的预热容器（避免死锁），每次创建后依赖 _create_new_container 自己把容器加入 self.containers
            created = 0
            for _ in range(to_create):
                try:
                    new_id = self._create_new_container()
                    if new_id:
                        created += 1
                        print(f"[Cleaner] Pre-warmed container {new_id[:12]} created.")
                    else:
                        print("[Cleaner] Failed to create pre-warm container (check logs).")
                    # 小睡以防速率过高并给 docker 一点缓冲
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[Cleaner] Exception while creating pre-warm container: {e}")
                    # 如果创建失败，继续尝试下一个（或根据策略 break）
                    continue

            # （可选）再次检查、限制创建数量或记录指标
            if created:
                print(f"[Cleaner] Created {created} pre-warm containers for {self.function_name}.")

    def stop_all_containers(self):
        # 立即设置停止事件，并尝试等待 cleaner 线程短时间，但不要无限等待
        self._cleaner_stop_event.set()
        # self.cleaner_thread.join(timeout=5) # 尝试等待 cleaner 退出，但不是强制要求
        
        print(f"Stopping all containers for {self.function_name}...")
        containers_to_stop = []
        with self.lock:
            # 复制一份，因为在迭代时可能会修改 self.containers
            containers_to_stop = list(self.containers.items()) 
            self.containers.clear() # 清空内部记录，避免再次操作

        for container_id, data in containers_to_stop:
            self._remove_container(container_id, data["container_obj"])
        print(f"All containers for {self.function_name} stopped and removed.")