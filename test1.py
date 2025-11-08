# test.py
"""
Test script for the separated controller:
1. POST /create_manager to create 'matmul' manager
2. Poll /manager_status/matmul until idle >= min_idle_containers or timeout
3. Send N concurrent requests to /dispatch/matmul and print results
4. Poll status and print container list
"""
import requests, time, threading, sys

CONTROLLER = "http://127.0.0.1:5000"
FUNCTION = "matmul"
CREATE_PATH = f"{CONTROLLER}/create_manager"
STATUS_PATH = f"{CONTROLLER}/manager_status/{FUNCTION}"
DISPATCH_PATH = f"{CONTROLLER}/dispatch/{FUNCTION}"

# Configuration for creation (adjust image_name to your local image)
CREATE_BODY = {
    "function_name": FUNCTION,
    "image_name": "myimage:latest",   # 修改为本地镜像名
    "container_port": 5000,
    "host_port_start": 8000,
    "idle_timeout": 15,
    "min_idle_containers": 2
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

def create_manager():
    print("Creating manager:", CREATE_BODY)
    r = requests.post(CREATE_PATH, json=CREATE_BODY, timeout=10)
    print("create response:", r.status_code, r.text)

def wait_for_prewarm(min_idle=2, timeout=60):
    print(f"Waiting up to {timeout}s for pre-warm: need {min_idle} idle containers...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(STATUS_PATH, timeout=3)
            if r.status_code == 200:
                j = r.json()
                print("status:", j)
                if j.get("idle", 0) >= min_idle:
                    print("Pre-warm condition satisfied.")
                    return True
        except Exception as e:
            print("status check error:", e)
        time.sleep(1.0)
    print("Pre-warm timed out.")
    return False

def send_request(i, tag=""):
    payload = {"param": 1000 + i}
    try:
        r = requests.post(DISPATCH_PATH, json=payload, timeout=60)
        try:
            body = r.json()
        except Exception:
            body = r.text
        print(f"[{tag}] req#{i} -> {r.status_code} {body}")
    except Exception as e:
        print(f"[{tag}] req#{i} failed: {e}")

def run_concurrent(n=3, stagger=0.1):
    threads = []
    for i in range(1, n+1):
        t = threading.Thread(target=send_request, args=(i, "main"))
        threads.append(t)
        t.start()
        # time.sleep(stagger) 这句可以注释掉, 保证三个请求同时到来
    for t in threads:
        t.join()

def main():
    if not wait_for_controller():
        sys.exit(1)

    # 1. create manager
    create_manager()

    # 2. wait for pre-warm
    if not wait_for_prewarm(min_idle=CREATE_BODY["min_idle_containers"], timeout=120):
        print("Pre-warm not ready, continuing anyway (you may inspect logs).")

    # 3. show current manager status
    try:
        r = requests.get(STATUS_PATH, timeout=3)
        print("Final status before dispatch:", r.status_code, r.text)
    except Exception as e:
        print("Failed to fetch status:", e)

    # 4. send concurrent requests
    print("Sending concurrent requests...")
    run_concurrent(n=3)

    # 5. final status
    time.sleep(1)
    try:
        r = requests.get(STATUS_PATH, timeout=3)
        print("Status after requests:", r.status_code, r.text)
    except Exception as e:
        print("Status fetch error:", e)

if __name__ == "__main__":
    main()
