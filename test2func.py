# test.py
"""
Test script for the separated controller:
1. Create managers for 'matmul' and 'image'
2. Poll /manager_status/<function> until idle >= min_idle_containers or timeout
3. Send 3 concurrent requests to /dispatch/matmul
4. Send 4 synchronous requests to /dispatch/image (one-by-one)
5. Poll status and print container list
"""
import requests, time, threading, sys

CONTROLLER = "http://127.0.0.1:5000"

# Primary functions
FUNC_MATMUL = "matmul"
FUNC_IMAGE = "image"

CREATE_PATH = f"{CONTROLLER}/create_manager"
STATUS_PATH_TMPL = f"{CONTROLLER}/manager_status/{{}}"
DISPATCH_PATH_TMPL = f"{CONTROLLER}/dispatch/{{}}"

# Configuration for creation (adjust image_name to your local images)
CREATE_BODY_MATMUL = {
    "function_name": FUNC_MATMUL,
    "image_name": "myimage:latest",   # 修改为本地 matmul 镜像名
    "container_port": 5000,
    "host_port_start": 8000,
    "idle_timeout": 15,
    "min_idle_containers": 2
}

CREATE_BODY_IMAGE = {
    "function_name": FUNC_IMAGE,
    "image_name": "myimage:latest",  # 修改为本地 image 镜像名
    "container_port": 5000,
    "host_port_start": 8100,
    "idle_timeout": 15,
    "min_idle_containers": 1
}

# How long to wait for controller itself to be reachable
CTRL_WAIT = 20.0

def wait_for_controller(timeout=CTRL_WAIT):
    print("Waiting for controller to be reachable...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(CONTROLLER, timeout=2)
            print("Controller reachable (root):", r.status_code)
            return True
        except Exception:
            # try create endpoint as indicator
            try:
                r = requests.get(CREATE_PATH, timeout=2)
                return True
            except Exception:
                pass
        time.sleep(0.5)
    print("Controller not reachable. Start controller.py first.")
    return False

def create_manager_for(body):
    name = body.get("function_name", "<unknown>")
    print(f"Creating manager for '{name}': {body}")
    try:
        r = requests.post(CREATE_PATH, json=body, timeout=10)
        print(f"create response for '{name}':", r.status_code, r.text)
    except Exception as e:
        print(f"Failed to create manager '{name}': {e}")

def wait_for_prewarm_for(function_name, min_idle=1, timeout=60):
    status_path = STATUS_PATH_TMPL.format(function_name)
    print(f"Waiting up to {timeout}s for pre-warm of '{function_name}': need {min_idle} idle containers...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(status_path, timeout=3)
            if r.status_code == 200:
                j = r.json()
                print(f"[{function_name}] status:", j)
                if j.get("idle", 0) >= min_idle:
                    print(f"[{function_name}] Pre-warm condition satisfied.")
                    return True
        except Exception as e:
            print(f"[{function_name}] status check error:", e)
        time.sleep(1.0)
    print(f"[{function_name}] Pre-warm timed out.")
    return False

def send_request(function_name, i, tag=""):
    dispatch_path = DISPATCH_PATH_TMPL.format(function_name)
    payload = {"param": (1000 + i), "from": tag}
    try:
        r = requests.post(dispatch_path, json=payload, timeout=60)
        try:
            body = r.json()
        except Exception:
            body = r.text
        print(f"[{function_name}][{tag}] req#{i} -> {r.status_code} {body}")
    except Exception as e:
        print(f"[{function_name}][{tag}] req#{i} failed: {e}")

def run_concurrent_matmul(n=3):
    threads = []
    for i in range(1, n+1):
        t = threading.Thread(target=send_request, args=(FUNC_MATMUL, i, "matmul-concur"))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

def run_concurrent_image(n=4):
    threads = []
    for i in range(1, n+1):
        t = threading.Thread(target=send_request, args=(FUNC_IMAGE, i, "image-concur"))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

def print_manager_status(function_name):
    try:
        r = requests.get(STATUS_PATH_TMPL.format(function_name), timeout=3)
        print(f"[{function_name}] manager status ->", r.status_code, r.text)
    except Exception as e:
        print(f"[{function_name}] status fetch error:", e)

def main():
    if not wait_for_controller():
        sys.exit(1)

    # 1. create both managers
    create_manager_for(CREATE_BODY_MATMUL)
    create_manager_for(CREATE_BODY_IMAGE)

    # 2. wait for pre-warm for both (adjust timeouts as needed)
    ready_matmul = wait_for_prewarm_for(FUNC_MATMUL, min_idle=CREATE_BODY_MATMUL["min_idle_containers"], timeout=120)
    if not ready_matmul:
        print("Matmul pre-warm not ready, continuing anyway (you may inspect logs).")

    ready_image = wait_for_prewarm_for(FUNC_IMAGE, min_idle=CREATE_BODY_IMAGE["min_idle_containers"], timeout=60)
    if not ready_image:
        print("Image pre-warm not ready, continuing anyway (you may inspect logs).")

    # 3. show current manager statuses
    print_manager_status(FUNC_MATMUL)
    print_manager_status(FUNC_IMAGE)

    # 4. send concurrent requests to matmul
    print("\n--- Sending concurrent requests to matmul ---")
    run_concurrent_matmul(n=3)

    # 5. synchronous requests to image
    print("\n--- Sending synchronous requests to image ---")
    run_concurrent_image(n=4)

    # 6. final statuses
    time.sleep(1)
    print("\nFinal statuses:")
    print_manager_status(FUNC_MATMUL)
    print_manager_status(FUNC_IMAGE)

if __name__ == "__main__":
    main()
